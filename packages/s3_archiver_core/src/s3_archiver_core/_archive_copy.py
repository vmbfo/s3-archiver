from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import replace
from threading import Lock

from s3_archiver_core._archive_copy_group import archive_size_matches
from s3_archiver_core._archive_copy_group import copy_group as copy_group
from s3_archiver_core._archive_copy_routes import (
    archive_groups_for_route,
    direct_entries_for_route,
    direct_entry_count,
)
from s3_archiver_core._archive_identity import stable_identity_value
from s3_archiver_core._archive_manifest_models import ArchiveGroup, ArchiveManifest, ManifestEntry
from s3_archiver_core._archive_manifest_store import SQLiteManifestStore
from s3_archiver_core._archive_object_activity import (
    entry_activity_watchdog,
    log_large_entry,
)
from s3_archiver_core._archive_parallel import run_parallel_items
from s3_archiver_core._archive_phase_progress import PhaseProgress
from s3_archiver_core._archive_protocols import ArchiveBucket
from s3_archiver_core._archive_verify_direct import verify_direct_entry
from s3_archiver_core.archive_group_metadata import (
    existing_archive_verified,
    group_metadata,
)
from s3_archiver_core.archive_progress import ProgressLogger
from s3_archiver_core.archive_result import ArchivePhaseResult
from s3_archiver_core.archive_routes import ArchiveRoute, DebugLogger
from s3_archiver_core.archive_transfer import (
    archive_metadata,
    fingerprint_from_metadata,
    select_transfer_strategy,
)
from s3_archiver_core.s3 import S3ObjectProperties

type GroupIdentity = tuple[object | None, str, str]
type ProgressAdvance = Callable[[int], None]


def copy_phase(
    manifest: ArchiveManifest,
    routes_by_name: dict[str, ArchiveRoute],
    debug_logger: DebugLogger | None,
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
    progress_logger: ProgressLogger | None = None,
    collect_verified: bool = True,
) -> tuple[ArchivePhaseResult, Sequence[ArchiveGroup], Sequence[ManifestEntry]]:
    """Copy direct entries and daily archive groups with one worker per route."""

    if not routes_by_name:
        return ArchivePhaseResult("copy"), (), ()
    progress = PhaseProgress("copy", len(manifest.entries), progress_logger)
    verified: dict[GroupIdentity, ArchiveGroup] = {}
    verified_entries = SQLiteManifestStore.temporary(
        next(iter(routes_by_name.values())).destination.temp_dir
    )
    result_lock = Lock()
    route_names = tuple(route.name for route in routes_by_name.values())

    def worker(route_name: str) -> tuple[str, ...]:
        route = routes_by_name[route_name]
        failures: list[str] = []
        for entry in direct_entries_for_route(manifest.entries, route_name):
            failure, copied = copy_direct_entry(route, entry, debug_logger)
            progress.advance()
            if failure is not None:
                failures.append(failure)
                continue
            if copied and collect_verified:
                with result_lock:
                    verified_entries.add_entry(entry)
        for group in archive_groups_for_route(manifest.archive_groups, route_name):
            failure, copied = copy_group(
                route.source,
                route.destination,
                group,
                debug_logger,
                progress_logger=progress.advance,
            )
            if failure is not None:
                failures.append(failure)
                continue
            if copied and collect_verified:
                with result_lock:
                    verified[_group_identity(group)] = group
        return tuple(failures)

    phase = ArchivePhaseResult(
        "copy", run_parallel_items(route_names, worker, timed_out, time_remaining)
    )
    if not collect_verified:
        if phase.ok:
            return phase, manifest.archive_groups, manifest.entries
        return phase, (), ()
    with result_lock:
        verified_keys = frozenset(verified)
        verified_entries.commit_entries()
    verified_groups = (
        group for group in manifest.archive_groups if _group_identity(group) in verified_keys
    )
    return (
        phase,
        tuple(verified_groups),
        verified_entries.entries if len(verified_entries.entries) else (),
    )


def copy_direct_entry(
    route: ArchiveRoute,
    entry: ManifestEntry,
    debug_logger: DebugLogger | None,
) -> tuple[str | None, bool]:
    destination_key = entry.destination_key
    try:
        hydrated = _entry_with_current_source_properties(route.source, entry)
        metadata = archive_metadata(hydrated)
        existing = route.destination.head_object(destination_key)
        if existing is not None:
            verified = verify_direct_entry(route, hydrated, existing)
            if verified.ok:
                return None, True
            if not _direct_destination_refreshable(hydrated, existing):
                detail = f"{verified.detail}; remove conflicting destination"
                return f"{destination_key}: {detail}", False
        strategy = select_transfer_strategy(entry.size, route.transfer_capabilities)
        if debug_logger is not None:
            debug_logger(hydrated, strategy)
        log_large_entry(
            operation="direct_copy",
            entry=hydrated,
            destination_bucket=route.destination.bucket,
            destination_key=destination_key,
        )
        with entry_activity_watchdog(
            operation="direct_copy",
            entry=hydrated,
            destination_bucket=route.destination.bucket,
            destination_key=destination_key,
        ):
            route.destination.copy_from(
                route.source,
                hydrated.source_bucket,
                hydrated.key,
                hydrated.version_id,
                hydrated.object.properties,
                destination_key,
                metadata,
                strategy,
            )
    except Exception as exc:
        return f"{destination_key}: {exc}", False
    verified = verify_direct_entry(route, hydrated, route.destination.head_object(destination_key))
    if verified.ok:
        return None, True
    return f"{destination_key}: {verified.detail}", False


def verify_phase(
    groups: Sequence[ArchiveGroup],
    entries: Sequence[ManifestEntry],
    routes_by_name: dict[str, ArchiveRoute],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
    progress_logger: ProgressLogger | None = None,
    on_verified: Callable[[ManifestEntry], None] | None = None,
) -> ArchivePhaseResult:
    route_names = tuple(route.name for route in routes_by_name.values())
    progress = PhaseProgress("verify", len(groups) + direct_entry_count(entries), progress_logger)

    def worker(route_name: str) -> tuple[str, ...]:
        route = routes_by_name[route_name]
        failures: list[str] = []
        for group in archive_groups_for_route(groups, route_name):
            try:
                metadata = group_metadata(group)
                existing = route.destination.head_object(group.destination_archive_key)
                if existing is None:
                    failures.append(f"{group.destination_archive_key}: destination missing")
                elif not archive_size_matches(existing) or not existing_archive_verified(
                    route.destination, group.destination_archive_key, existing.metadata, metadata
                ):
                    failures.append(f"{group.destination_archive_key}: archive verification failed")
                elif on_verified is not None:
                    for entry in group.entries:
                        on_verified(entry)
            except Exception as exc:
                failures.append(f"{group.destination_archive_key}: {exc}")
            progress.advance()
        for entry in direct_entries_for_route(entries, route_name):
            try:
                hydrated = _entry_with_current_source_properties(route.source, entry)
                verified = verify_direct_entry(
                    route, hydrated, route.destination.head_object(entry.destination_key)
                )
                if not verified.ok:
                    failures.append(f"{entry.destination_key}: {verified.detail}")
                elif on_verified is not None:
                    on_verified(entry)
            except Exception as exc:
                failures.append(f"{entry.destination_key}: {exc}")
            progress.advance()
        return tuple(failures)

    return ArchivePhaseResult(
        "verify", run_parallel_items(route_names, worker, timed_out, time_remaining)
    )


def _entry_with_current_source_properties(
    source: ArchiveBucket, entry: ManifestEntry
) -> ManifestEntry:
    properties = source.head_object(entry.key, entry.version_id)
    if properties is None:
        raise FileNotFoundError(f"{entry.key}: listed source object disappeared before copy")
    listed = replace(entry.object, properties=properties)
    return replace(entry, object=listed)


def _direct_destination_refreshable(entry: ManifestEntry, existing: S3ObjectProperties) -> bool:
    fingerprint = fingerprint_from_metadata(existing.metadata)
    return (
        fingerprint is not None
        and fingerprint.source_bucket == entry.source_bucket
        and fingerprint.source_identity == stable_identity_value(entry.source_identity)
        and fingerprint.source_key == entry.key
    )


def _group_identity(group: ArchiveGroup) -> GroupIdentity:
    return (group.destination_identity, group.destination_bucket, group.destination_archive_key)
