from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path
from threading import Lock

from s3_archiver_core._archive_copy_routes import (
    archive_groups_for_route,
    direct_entries_for_route,
    direct_entry_count,
)
from s3_archiver_core._archive_env import bool_env
from s3_archiver_core._archive_manifest_models import ArchiveGroup, ArchiveManifest, ManifestEntry
from s3_archiver_core._archive_parallel import run_parallel_items
from s3_archiver_core._archive_phase_progress import PhaseProgress
from s3_archiver_core._archive_protocols import ArchiveBucket
from s3_archiver_core.archive_group_metadata import (
    ARCHIVE_SHA256_METADATA_KEY,
    existing_archive_verified,
    group_metadata,
    uploaded_archive_verified,
)
from s3_archiver_core.archive_progress import ProgressLogger
from s3_archiver_core.archive_result import ArchivePhaseResult
from s3_archiver_core.archive_routes import ArchiveRoute, DebugLogger
from s3_archiver_core.archive_tar import sha256_file, write_tar_gz_archive
from s3_archiver_core.archive_transfer import (
    VerificationResult,
    archive_metadata,
    select_transfer_strategy,
    verify_destination,
    verify_destination_checksum,
    verify_destination_content,
)
from s3_archiver_core.s3 import S3ObjectProperties
from s3_archiver_core.temp_files import TRANSFER_TEMP_PREFIX

type GroupIdentity = tuple[object | None, str, str]
type EntryIdentity = tuple[object | None, str, str, str | None]
type ProgressAdvance = Callable[[int], None]
_DIRECT_CONTENT_VERIFY_ENV = "ARCHIVER_DIRECT_CONTENT_VERIFY"


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

    progress = PhaseProgress("copy", len(manifest.entries), progress_logger)
    verified: dict[GroupIdentity, ArchiveGroup] = {}
    verified_entries: dict[EntryIdentity, ManifestEntry] = {}
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
                    verified_entries[_entry_identity(entry)] = entry
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
        entry_keys = frozenset(verified_entries)
    verified_groups = (
        group for group in manifest.archive_groups if _group_identity(group) in verified_keys
    )
    direct_entries = (entry for entry in manifest.entries if _entry_identity(entry) in entry_keys)
    return phase, tuple(verified_groups), tuple(direct_entries)


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
            return f"{destination_key}: {verified.detail}", False
        strategy = select_transfer_strategy(entry.size, route.transfer_capabilities)
        if debug_logger is not None:
            debug_logger(hydrated, strategy)
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


def verify_direct_entry(
    route: ArchiveRoute,
    entry: ManifestEntry,
    destination: S3ObjectProperties | None,
) -> VerificationResult:
    _ = route
    verified = verify_destination(entry, destination)
    if not verified.ok:
        return verified
    assert destination is not None
    checksum_verified = verify_destination_checksum(entry.object.properties, destination)
    if checksum_verified is not None:
        return checksum_verified
    if not bool_env(_DIRECT_CONTENT_VERIFY_ENV):
        return VerificationResult(True, "ok")
    return verify_destination_content(
        route.source.content_sha256(entry.key, entry.version_id),
        route.destination.content_sha256(entry.destination_key),
    )


def copy_group(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    group: ArchiveGroup,
    debug_logger: DebugLogger | None,
    *,
    progress_logger: ProgressAdvance | None = None,
) -> tuple[str | None, bool]:
    destination_key = group.destination_archive_key
    metadata = group_metadata(group)
    existing = destination.head_object(destination_key)
    if existing is not None:
        if existing_archive_verified(destination, destination_key, existing.metadata, metadata):
            _advance_group_progress(group, progress_logger)
            return None, True
        return f"{destination_key}: archive verification failed", False
    archive_path: Path | None = None
    try:
        if debug_logger is not None:
            for entry in group.entries:
                debug_logger(entry, "deterministic_tar_gzip")
        destination.temp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "wb", delete=False, dir=destination.temp_dir, prefix=TRANSFER_TEMP_PREFIX
        ) as archive_file:
            archive_path = Path(archive_file.name)
        write_tar_gz_archive(
            source,
            group,
            archive_path,
            progress_logger=None if progress_logger is None else lambda: progress_logger(1),
        )
        upload_metadata = dict(metadata)
        upload_metadata[ARCHIVE_SHA256_METADATA_KEY] = sha256_file(archive_path)
        destination.upload_archive_file(destination_key, archive_path, upload_metadata)
    except Exception as exc:
        return f"{destination_key}: {exc}", False
    finally:
        if archive_path is not None:  # pragma: no branch
            archive_path.unlink(missing_ok=True)
    verified = destination.head_object(destination_key)
    if verified is None:
        return f"{destination_key}: destination missing", False
    if uploaded_archive_verified(destination, destination_key, verified.metadata, upload_metadata):
        return None, True
    return f"{destination_key}: archive verification failed", False


def verify_phase(
    groups: Sequence[ArchiveGroup],
    entries: Sequence[ManifestEntry],
    routes_by_name: dict[str, ArchiveRoute],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
    progress_logger: ProgressLogger | None = None,
) -> ArchivePhaseResult:
    route_names = tuple(route.name for route in routes_by_name.values())
    progress = PhaseProgress("verify", len(groups) + direct_entry_count(entries), progress_logger)

    def worker(route_name: str) -> tuple[str, ...]:
        route = routes_by_name[route_name]
        failures: list[str] = []
        for group in archive_groups_for_route(groups, route_name):
            metadata = group_metadata(group)
            existing = route.destination.head_object(group.destination_archive_key)
            if existing is None:
                failures.append(f"{group.destination_archive_key}: destination missing")
            elif not existing_archive_verified(
                route.destination, group.destination_archive_key, existing.metadata, metadata
            ):
                failures.append(f"{group.destination_archive_key}: archive verification failed")
            progress.advance()
        for entry in direct_entries_for_route(entries, route_name):
            hydrated = _entry_with_current_source_properties(route.source, entry)
            verified = verify_direct_entry(
                route, hydrated, route.destination.head_object(entry.destination_key)
            )
            if not verified.ok:
                failures.append(f"{entry.destination_key}: {verified.detail}")
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


def _advance_group_progress(group: ArchiveGroup, progress_logger: ProgressAdvance | None) -> None:
    if progress_logger is None:
        return
    progress_logger(group.source_count or len(group.entries))


def _group_identity(group: ArchiveGroup) -> GroupIdentity:
    return (group.destination_identity, group.destination_bucket, group.destination_archive_key)


def _entry_identity(entry: ManifestEntry) -> EntryIdentity:
    return (
        entry.destination_identity,
        entry.destination_bucket,
        entry.destination_key,
        entry.version_id,
    )
