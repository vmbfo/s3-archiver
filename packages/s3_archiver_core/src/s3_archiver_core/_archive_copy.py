from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread

from s3_archiver_core._archive_manifest_models import ArchiveGroup, ArchiveManifest, ManifestEntry
from s3_archiver_core._archive_protocols import ArchiveBucket
from s3_archiver_core._archive_routes import ArchiveRoute, DebugLogger
from s3_archiver_core.archive_group_metadata import (
    ARCHIVE_SHA256_METADATA_KEY,
    existing_archive_verified,
    group_metadata,
    uploaded_archive_verified,
)
from s3_archiver_core.archive_result import ArchivePhaseResult
from s3_archiver_core.archive_tar import sha256_file, write_tar_gz_archive
from s3_archiver_core.archive_transfer import (
    VerificationResult,
    archive_metadata,
    select_transfer_strategy,
    verify_destination,
    verify_destination_content,
)
from s3_archiver_core.s3 import S3ObjectProperties
from s3_archiver_core.temp_files import TRANSFER_TEMP_PREFIX

type GroupIdentity = tuple[object | None, str, str]
type EntryIdentity = tuple[object | None, str, str, str | None]


def copy_phase(
    manifest: ArchiveManifest,
    routes_by_name: dict[str, ArchiveRoute],
    debug_logger: DebugLogger | None,
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> tuple[ArchivePhaseResult, tuple[ArchiveGroup, ...], tuple[ManifestEntry, ...]]:
    """Copy direct entries and daily archive groups with one worker per route."""

    verified: dict[GroupIdentity, ArchiveGroup] = {}
    verified_entries: dict[EntryIdentity, ManifestEntry] = {}
    result_lock = Lock()
    route_names = tuple(route.name for route in routes_by_name.values())

    def worker(route_name: str) -> tuple[str, ...]:
        route = routes_by_name[route_name]
        failures: list[str] = []
        for entry in _route_direct_entries(manifest, route_name):
            failure, copied = copy_direct_entry(route, entry, debug_logger)
            if failure is not None:
                failures.append(failure)
                continue
            if copied:
                with result_lock:
                    verified_entries[_entry_identity(entry)] = entry
        for group in _route_archive_groups(manifest, route_name):
            failure, copied = copy_group(route.source, route.destination, group, debug_logger)
            if failure is not None:
                failures.append(failure)
                continue
            if copied:
                with result_lock:
                    verified[_group_identity(group)] = group
        return tuple(failures)

    phase = ArchivePhaseResult(
        "copy", run_route_workers(route_names, worker, timed_out, time_remaining)
    )
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
        metadata = archive_metadata(entry)
        existing = route.destination.head_object(destination_key)
        if existing is not None:
            verified = verify_direct_entry(route, entry, existing)
            if verified.ok:
                return None, True
            return f"{destination_key}: {verified.detail}", False
        strategy = select_transfer_strategy(entry.size, route.transfer_capabilities)
        if debug_logger is not None:
            debug_logger(entry, strategy)
        route.destination.copy_from(
            route.source,
            entry.source_bucket,
            entry.key,
            entry.version_id,
            entry.object.properties,
            destination_key,
            metadata,
            strategy,
        )
    except Exception as exc:
        return f"{destination_key}: {exc}", False
    verified = verify_direct_entry(route, entry, route.destination.head_object(destination_key))
    if verified.ok:
        return None, True
    return f"{destination_key}: {verified.detail}", False


def verify_direct_entry(
    route: ArchiveRoute,
    entry: ManifestEntry,
    destination: S3ObjectProperties | None,
) -> VerificationResult:
    verified = verify_destination(entry, destination)
    if not verified.ok:
        return verified
    return verify_destination_content(
        route.source.content_sha256(entry.key, entry.version_id),
        route.destination.content_sha256(entry.destination_key),
    )


def copy_group(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    group: ArchiveGroup,
    debug_logger: DebugLogger | None,
) -> tuple[str | None, bool]:
    destination_key = group.destination_archive_key
    metadata = group_metadata(group)
    existing = destination.head_object(destination_key)
    if existing is not None:
        if existing_archive_verified(destination, destination_key, existing.metadata, metadata):
            return None, True
        return f"{destination_key}: archive verification failed", False
    archive_path: Path | None = None
    try:
        for entry in group.entries:
            if debug_logger is not None:
                debug_logger(entry, "deterministic_tar_gzip")
        destination.temp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "wb", delete=False, dir=destination.temp_dir, prefix=TRANSFER_TEMP_PREFIX
        ) as archive_file:
            archive_path = Path(archive_file.name)
        write_tar_gz_archive(source, group, archive_path)
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
    groups: tuple[ArchiveGroup, ...],
    entries: tuple[ManifestEntry, ...],
    routes_by_name: dict[str, ArchiveRoute],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> ArchivePhaseResult:
    route_names = tuple(route.name for route in routes_by_name.values())

    def worker(route_name: str) -> tuple[str, ...]:
        route = routes_by_name[route_name]
        failures: list[str] = []
        for group in (item for item in groups if item.route_name == route_name):
            metadata = group_metadata(group)
            existing = route.destination.head_object(group.destination_archive_key)
            if existing is None:
                failures.append(f"{group.destination_archive_key}: destination missing")
            elif not existing_archive_verified(
                route.destination, group.destination_archive_key, existing.metadata, metadata
            ):
                failures.append(f"{group.destination_archive_key}: archive verification failed")
        for entry in (item for item in entries if item.route_name == route_name):
            verified = verify_destination(
                entry, route.destination.head_object(entry.destination_key)
            )
            if not verified.ok:
                failures.append(f"{entry.destination_key}: {verified.detail}")
        return tuple(failures)

    return ArchivePhaseResult(
        "verify", run_route_workers(route_names, worker, timed_out, time_remaining)
    )


def run_route_workers(
    route_names: tuple[str, ...],
    worker: Callable[[str], tuple[str, ...]],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> tuple[str, ...]:
    if not route_names:
        return ()
    if timed_out():
        return ("archive run timed out",)
    results: Queue[tuple[str, ...]] = Queue()
    for route_name in route_names:
        thread = Thread(
            target=_put_route_worker_result, args=(results, worker, route_name), daemon=True
        )
        thread.start()
    failures: list[str] = []
    pending = len(route_names)
    while pending:
        try:
            route_failures = results.get(timeout=time_remaining())
        except Empty:
            failures.append("archive run timed out")
            return tuple(failures)
        pending -= 1
        failures.extend(route_failures)
    return tuple(failures)


def _route_direct_entries(manifest: ArchiveManifest, route_name: str) -> tuple[ManifestEntry, ...]:
    return tuple(
        entry
        for entry in manifest.entries
        if entry.route_name == route_name and entry.copy_mode == "direct"
    )


def _group_identity(group: ArchiveGroup) -> GroupIdentity:
    return (group.destination_identity, group.destination_bucket, group.destination_archive_key)


def _entry_identity(entry: ManifestEntry) -> EntryIdentity:
    return (
        entry.destination_identity,
        entry.destination_bucket,
        entry.destination_key,
        entry.version_id,
    )


def _route_archive_groups(manifest: ArchiveManifest, route_name: str) -> tuple[ArchiveGroup, ...]:
    return tuple(group for group in manifest.archive_groups if group.route_name == route_name)


def _put_route_worker_result(
    results: Queue[tuple[str, ...]],
    worker: Callable[[str], tuple[str, ...]],
    route_name: str,
) -> None:
    try:
        failures = worker(route_name)
    except Exception as exc:
        failures = (f"{route_name}: {exc}",)
    results.put(failures)
