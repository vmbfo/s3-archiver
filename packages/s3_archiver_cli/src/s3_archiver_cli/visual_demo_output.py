"""Human-readable visual demo emitters."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import cast

from s3_archiver_core.archive_manifest import ArchiveManifest
from s3_archiver_core.settings import AppSettings

from s3_archiver_cli.archive_payloads import (
    archive_group_payloads,
    manifest_target_day,
    skipped_object_payloads,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]
type Emitter = Callable[[str], None]


def emit_intro(emit: Emitter, settings: AppSettings, log_file: Path, started: datetime) -> None:
    """Emit the visual demo heading and runtime context."""

    emit("== S3 Archiver Visual Demo ==")
    emit(f"source bucket: {settings.source.bucket}")
    emit(f"destination bucket: {settings.destination.bucket}")
    emit(f"cleanup enabled in settings: {settings.cleanup_enabled}")
    emit(f"log file: {log_file}")
    emit(f"run started at utc: {started.isoformat()}")


def emit_health(emit: Emitter, health: dict[str, JsonValue]) -> None:
    """Emit preflight health-check output."""

    emit("")
    emit("== Preflight ==")
    emit(f"status: {health['status']}")
    emit(f"checked_at: {health['checked_at']}")


def emit_snapshot(emit: Emitter, title: str, snapshot: dict[str, JsonValue]) -> None:
    """Emit source and destination bucket snapshot output."""

    emit("")
    emit(f"== {title} ==")
    emit(
        f"source objects: {snapshot['source_object_count']} "
        + f"(versioning={snapshot['source_versioning_state']})"
    )
    for row in cast(list[dict[str, JsonValue]], snapshot["source_objects"]):
        emit(
            "SOURCE "
            + f"key={row['key']} "
            + f"size={row['size']} "
            + f"last_modified={row['last_modified_utc']} "
            + f"eligible={row['eligible_for_follow_up']} "
            + f"present_in_destination={row['present_in_destination']}"
        )
    emit(
        f"destination objects: {snapshot['destination_object_count']} "
        + f"(versioning={snapshot['destination_versioning_state']})"
    )
    for row in cast(list[dict[str, JsonValue]], snapshot["destination_objects"]):
        source_modified = row["source_last_modified"]
        source_detail = f" source_last_modified={source_modified}" if source_modified else ""
        emit(
            f"DEST   key={row['key']} size={row['size']} "
            + f"last_modified={row['last_modified_utc']}{source_detail}"
        )


def emit_manifest(emit: Emitter, manifest: ArchiveManifest) -> None:
    """Emit manifest candidate and archive-group output."""

    emit("")
    emit("== Archive Candidates ==")
    emit(f"target day: {manifest_target_day(manifest)}")
    emit(f"retention cutoff utc: {manifest.retention_cutoff_utc.isoformat()}")
    groups = archive_group_payloads(manifest)
    skipped = skipped_object_payloads(manifest)
    emit(f"archive group count: {len(groups)}")
    emit(f"source object count: {len(manifest.entries)}")
    emit(f"skipped object count: {len(skipped)}")
    for group in groups:
        emit(
            "GROUP  "
            + f"target_day={group['target_day']} "
            + f"archive_root={group['archive_root']} "
            + f"destination_archive_key={group['destination_archive_key']} "
            + f"source_object_count={group['source_object_count']} "
            + f"skipped_object_count={group['skipped_object_count']}"
        )
    for item in skipped:
        emit(
            "SKIP   "
            + f"key={item['key']} "
            + f"reason={item['reason']} "
            + f"target_day={item['target_day']} "
            + f"archive_root={item['archive_root']}"
        )
    for entry in manifest.entries:
        emit(
            "SOURCE "
            + f"key={entry.key} "
            + f"size={entry.size} "
            + f"last_modified={entry.last_modified.isoformat()} "
            + f"version_id={entry.version_id}"
        )


def emit_archive_result(emit: Emitter, payload: dict[str, JsonValue]) -> None:
    """Emit archive result output."""

    emit("")
    emit("== Archive Result ==")
    emit(f"status: {payload['status']}")
    emit(f"target day: {payload.get('target_day')}")
    emit(f"archive count: {payload.get('archive_count')}")
    for group in cast(list[dict[str, JsonValue]], payload.get("archive_groups", [])):
        emit(
            "GROUP  "
            + f"destination_archive_key={group['destination_archive_key']} "
            + f"source_object_count={group['source_object_count']} "
            + f"skipped_object_count={group['skipped_object_count']} "
            + f"cleanup_status={group['cleanup_status']}"
        )
    phases = cast(dict[str, dict[str, JsonValue]], payload["phases"])
    for phase_name in ("list", "copy", "verify", "cleanup"):
        phase = phases[phase_name]
        emit(f"{phase_name}: status={phase['status']} failure_count={phase['failure_count']}")


def emit_cleanup_preview(emit: Emitter, cleanup_preview: dict[str, JsonValue]) -> None:
    """Emit cleanup preview output."""

    emit("")
    emit("== Cleanup Preview ==")
    emit(f"cleanup enabled in settings: {cleanup_preview['cleanup_enabled_in_settings']}")
    emit(f"preview manifest file: {cleanup_preview['manifest_file']}")
    emit(f"target day: {cleanup_preview.get('target_day')}")
    emit(f"archive count: {cleanup_preview.get('archive_count')}")
    emit(f"would delete object count: {cleanup_preview['object_count']}")
    for group in cast(list[dict[str, JsonValue]], cleanup_preview.get("archive_groups", [])):
        emit(
            "GROUP  "
            + f"destination_archive_key={group['destination_archive_key']} "
            + f"source_object_count={group['source_object_count']} "
            + f"skipped_object_count={group['skipped_object_count']} "
            + f"cleanup_status={group['cleanup_status']}"
        )
    for item in cast(list[dict[str, JsonValue]], cleanup_preview.get("skipped_objects", [])):
        emit("SKIP   " + f"key={item['key']} " + f"reason={item['reason']}")
    for row in cast(list[dict[str, JsonValue]], cleanup_preview["entries"]):
        emit(
            "DELETE "
            + f"key={row['key']} "
            + f"size={row['size']} "
            + f"last_modified={row['last_modified_utc']} "
            + f"version_id={row['version_id']}"
        )
