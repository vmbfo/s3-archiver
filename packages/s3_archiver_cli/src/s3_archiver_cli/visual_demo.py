"""Human-readable archive demo output backed by real S3 state."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from s3_archiver_core.archive_manifest import ArchiveManifest, ManifestEntry, build_archive_manifest
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.health import run_health_check
from s3_archiver_core.s3 import S3ListedObject, build_s3_client
from s3_archiver_core.settings import AppSettings
from s3_archiver_core.temp_files import prepare_runtime_temp_dir

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]
type ArchiveRunner = Callable[[AppSettings, Path], dict[str, JsonValue]]
type CleanupPreviewRunner = Callable[[AppSettings, Path], dict[str, JsonValue]]
type Emitter = Callable[[str], None]


def run_visual_demo(
    settings: AppSettings,
    log_file: Path,
    *,
    archive_runner: ArchiveRunner,
    cleanup_preview_runner: CleanupPreviewRunner,
    emit: Emitter,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    """Run a human-readable archive walkthrough against the configured buckets."""

    clock = _utc_now if now is None else now
    started = clock()
    prepare_runtime_temp_dir(settings.temp_dir)
    health = run_health_check(settings, log_file).as_dict()
    source = S3ArchiveBucket(
        build_s3_client(settings.source),
        settings.source.bucket,
        settings.temp_dir,
    )
    destination = S3ArchiveBucket(
        build_s3_client(settings.destination),
        settings.destination.bucket,
        settings.temp_dir,
    )
    options = ArchiveOptions.from_settings(settings)
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=started,
        retention_days=options.retention_days,
        versioning_state=source.versioning_state(),
        source_filter=options.source_filter,
    )
    eligible_keys = _manifest_key_set(manifest)
    before_snapshot = _snapshot_payload(source, destination, eligible_keys=eligible_keys)

    _emit_intro(emit, settings, log_file, started)
    _emit_health(emit, health)
    _emit_snapshot(emit, "Before archive", before_snapshot)
    _emit_manifest(emit, manifest)
    emit("Running archive workflow against the configured buckets...")
    archive_payload = archive_runner(settings, log_file)
    _emit_archive_result(emit, archive_payload)
    after_archive_snapshot = _snapshot_payload(source, destination, eligible_keys=set())
    _emit_snapshot(emit, "After archive", after_archive_snapshot)

    emit("Running cleanup preview without deleting source objects...")
    cleanup_payload = cleanup_preview_runner(settings, log_file)
    cleanup_preview = cast(dict[str, JsonValue], cleanup_payload["cleanup_preview"])
    cleanup_keys = _payload_key_set(cleanup_preview)
    _emit_cleanup_preview(emit, cleanup_preview)
    after_preview_snapshot = _snapshot_payload(source, destination, eligible_keys=cleanup_keys)
    _emit_snapshot(emit, "After cleanup preview", after_preview_snapshot)

    summary: dict[str, JsonValue] = {
        "status": "ok" if archive_payload.get("status") == "ok" else "error",
        "source_bucket": settings.source.bucket,
        "destination_bucket": settings.destination.bucket,
        "log_file": str(log_file),
        "cleanup_enabled_in_settings": settings.cleanup_enabled,
        "run_started_at_utc": started.isoformat(),
        "health": cast(dict[str, JsonValue], health),
        "archive_manifest": {
            "object_count": len(manifest.entries),
            "retention_cutoff_utc": manifest.retention_cutoff_utc.isoformat(),
            "entries": [_manifest_entry_payload(entry) for entry in manifest.entries],
        },
        "archive_result": archive_payload,
        "cleanup_preview": cleanup_preview,
        "snapshots": {
            "before_archive": before_snapshot,
            "after_archive": after_archive_snapshot,
            "after_cleanup_preview": after_preview_snapshot,
        },
        "cleanup_preview_left_bucket_state_unchanged": (
            _snapshot_bucket_state(after_archive_snapshot)
            == _snapshot_bucket_state(after_preview_snapshot)
        ),
    }
    emit("Demo summary JSON follows on the next line.")
    emit(json.dumps(summary, sort_keys=True))
    return summary


def _emit_intro(emit: Emitter, settings: AppSettings, log_file: Path, started: datetime) -> None:
    emit("== S3 Archiver Visual Demo ==")
    emit(f"source bucket: {settings.source.bucket}")
    emit(f"destination bucket: {settings.destination.bucket}")
    emit(f"cleanup enabled in settings: {settings.cleanup_enabled}")
    emit(f"log file: {log_file}")
    emit(f"run started at utc: {started.isoformat()}")


def _emit_health(emit: Emitter, health: dict[str, object]) -> None:
    emit("")
    emit("== Preflight ==")
    emit(f"status: {health['status']}")
    emit(f"checked_at: {health['checked_at']}")


def _emit_snapshot(emit: Emitter, title: str, snapshot: dict[str, JsonValue]) -> None:
    emit("")
    emit(f"== {title} ==")
    emit(
        "source objects: "
        f"{snapshot['source_object_count']} "
        f"(versioning={snapshot['source_versioning_state']})"
    )
    for row in cast(list[dict[str, JsonValue]], snapshot["source_objects"]):
        emit(
            "SOURCE "
            f"key={row['key']} "
            f"size={row['size']} "
            f"last_modified={row['last_modified_utc']} "
            f"eligible={row['eligible_for_follow_up']} "
            f"present_in_destination={row['present_in_destination']}"
        )
    emit(
        "destination objects: "
        f"{snapshot['destination_object_count']} "
        f"(versioning={snapshot['destination_versioning_state']})"
    )
    for row in cast(list[dict[str, JsonValue]], snapshot["destination_objects"]):
        emit(f"DEST   key={row['key']} size={row['size']} last_modified={row['last_modified_utc']}")


def _emit_manifest(emit: Emitter, manifest: ArchiveManifest) -> None:
    emit("")
    emit("== Archive Candidates ==")
    emit(f"retention cutoff utc: {manifest.retention_cutoff_utc.isoformat()}")
    emit(f"eligible object count: {len(manifest.entries)}")
    for entry in manifest.entries:
        emit(
            "COPY   "
            f"key={entry.key} "
            f"size={entry.size} "
            f"last_modified={entry.last_modified.isoformat()} "
            f"version_id={entry.version_id}"
        )


def _emit_archive_result(emit: Emitter, payload: dict[str, JsonValue]) -> None:
    emit("")
    emit("== Archive Result ==")
    emit(f"status: {payload['status']}")
    phases = cast(dict[str, dict[str, JsonValue]], payload["phases"])
    for phase_name in ("list", "copy", "verify", "cleanup"):
        phase = phases[phase_name]
        emit(f"{phase_name}: status={phase['status']} failure_count={phase['failure_count']}")


def _emit_cleanup_preview(emit: Emitter, cleanup_preview: dict[str, JsonValue]) -> None:
    emit("")
    emit("== Cleanup Preview ==")
    emit(f"cleanup enabled in settings: {cleanup_preview['cleanup_enabled_in_settings']}")
    emit(f"preview manifest file: {cleanup_preview['manifest_file']}")
    emit(f"would delete object count: {cleanup_preview['object_count']}")
    for row in cast(list[dict[str, JsonValue]], cleanup_preview["entries"]):
        emit(
            "DELETE "
            f"key={row['key']} "
            f"size={row['size']} "
            f"last_modified={row['last_modified_utc']} "
            f"version_id={row['version_id']}"
        )


def _snapshot_payload(
    source: S3ArchiveBucket,
    destination: S3ArchiveBucket,
    *,
    eligible_keys: set[tuple[str, str | None]],
) -> dict[str, JsonValue]:
    source_state = source.versioning_state()
    destination_state = destination.versioning_state()
    source_objects = sorted(source.list_source_objects(source_state), key=_object_sort_key)
    destination_objects = sorted(
        destination.list_source_objects(destination_state),
        key=_object_sort_key,
    )
    destination_keys = {(item.key, item.version_id) for item in destination_objects}
    return {
        "source_versioning_state": source_state,
        "destination_versioning_state": destination_state,
        "source_object_count": len(source_objects),
        "destination_object_count": len(destination_objects),
        "source_objects": [
            _listed_object_payload(
                item,
                eligible_for_follow_up=(item.key, item.version_id) in eligible_keys,
                present_in_destination=(item.key, item.version_id) in destination_keys
                or (item.key, None) in destination_keys,
            )
            for item in source_objects
        ],
        "destination_objects": [
            _listed_object_payload(
                item,
                eligible_for_follow_up=False,
                present_in_destination=True,
            )
            for item in destination_objects
        ],
    }


def _listed_object_payload(
    item: S3ListedObject,
    *,
    eligible_for_follow_up: bool,
    present_in_destination: bool,
) -> dict[str, JsonValue]:
    return {
        "key": item.key,
        "size": item.size,
        "last_modified_utc": item.last_modified.isoformat(),
        "etag": item.etag,
        "version_id": item.version_id,
        "eligible_for_follow_up": eligible_for_follow_up,
        "present_in_destination": present_in_destination,
    }


def _manifest_entry_payload(entry: ManifestEntry) -> dict[str, JsonValue]:
    return {
        "key": entry.key,
        "size": entry.size,
        "last_modified_utc": entry.last_modified.isoformat(),
        "version_id": entry.version_id,
        "etag": entry.etag,
        "source_bucket": entry.source_bucket,
    }


def _manifest_key_set(manifest: ArchiveManifest) -> set[tuple[str, str | None]]:
    return {(entry.key, entry.version_id) for entry in manifest.entries}


def _payload_key_set(cleanup_preview: dict[str, JsonValue]) -> set[tuple[str, str | None]]:
    entries = cast(list[dict[str, JsonValue]], cleanup_preview["entries"])
    return {
        (
            str(entry["key"]),
            None if entry["version_id"] is None else str(entry["version_id"]),
        )
        for entry in entries
    }


def _object_sort_key(item: S3ListedObject) -> tuple[str, str]:
    return (item.key, "" if item.version_id is None else item.version_id)


def _snapshot_bucket_state(snapshot: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        "source_versioning_state": snapshot["source_versioning_state"],
        "destination_versioning_state": snapshot["destination_versioning_state"],
        "source_object_count": snapshot["source_object_count"],
        "destination_object_count": snapshot["destination_object_count"],
        "source_objects": [
            {
                key: value
                for key, value in cast(dict[str, JsonValue], row).items()
                if key != "eligible_for_follow_up"
            }
            for row in cast(list[dict[str, JsonValue]], snapshot["source_objects"])
        ],
        "destination_objects": snapshot["destination_objects"],
    }


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
