"""Human-readable archive demo output backed by real S3 state."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from s3_archiver_core.archive_manifest import build_route_archive_manifest
from s3_archiver_core.health import run_health_check
from s3_archiver_core.s3 import build_s3_client
from s3_archiver_core.settings import AppSettings
from s3_archiver_core.temp_files import prepare_runtime_temp_dir

from s3_archiver_cli import visual_demo_output as _output
from s3_archiver_cli._archive_routes import archive_routes_from_settings
from s3_archiver_cli.archive_payload_utils import JsonValue, json_list
from s3_archiver_cli.archive_payloads import (
    archive_group_payloads,
    destination_archive_keys,
    destination_keys,
    direct_entry_payloads,
    manifest_target_day,
    skipped_object_payloads,
)
from s3_archiver_cli.route_payloads import route_summary_payload
from s3_archiver_cli.visual_demo_snapshots import (
    manifest_destination_key_map as _manifest_destination_key_map,
)
from s3_archiver_cli.visual_demo_snapshots import manifest_entry_payload as _manifest_entry_payload
from s3_archiver_cli.visual_demo_snapshots import manifest_key_set as _manifest_key_set
from s3_archiver_cli.visual_demo_snapshots import snapshot_payload as _snapshot_payload

type ArchiveRunner = Callable[[AppSettings, Path], dict[str, JsonValue]]
type Emitter = Callable[[str], None]


def run_visual_demo(
    settings: AppSettings,
    log_file: Path,
    *,
    archive_runner: ArchiveRunner,
    emit: Emitter,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    """Run a human-readable archive walkthrough against the configured buckets."""

    clock = _utc_now if now is None else now
    started = clock()
    prepare_runtime_temp_dir(settings.temp_dir)
    health = cast(dict[str, JsonValue], run_health_check(settings, log_file).as_dict())
    routes = archive_routes_from_settings(settings, build_s3_client)
    manifest = build_route_archive_manifest(routes, run_started_at_utc=started)
    eligible_keys = _manifest_key_set(manifest)
    planned_destinations = _manifest_destination_key_map(manifest)
    before_snapshot = _snapshot_payload(
        routes,
        eligible_keys=eligible_keys,
        planned_destinations=planned_destinations,
    )

    _output.emit_intro(emit, settings, log_file, started)
    _output.emit_health(emit, health)
    _output.emit_snapshot(emit, "Before archive", before_snapshot)
    _output.emit_manifest(emit, manifest)
    emit("Running archive workflow against the configured buckets...")
    archive_payload = archive_runner(settings, log_file)
    _output.emit_archive_result(emit, archive_payload)
    after_archive_snapshot = _snapshot_payload(
        routes,
        eligible_keys=set(),
        planned_destinations=planned_destinations,
    )
    _output.emit_snapshot(emit, "After archive", after_archive_snapshot)
    archive_groups = archive_group_payloads(manifest)
    direct_entries = direct_entry_payloads(manifest)
    skipped_objects = skipped_object_payloads(manifest)
    archive_days = sorted({str(group["target_day"]) for group in archive_groups})
    archive_days_payload = [cast(JsonValue, day) for day in archive_days]
    archive_keys = destination_archive_keys(archive_groups)
    all_destination_keys = destination_keys(archive_groups, direct_entries)
    archive_manifest: dict[str, JsonValue] = {
        "object_count": len(manifest.entries),
        "target_day": manifest_target_day(manifest),
        "archive_days": archive_days_payload,
        "archive_count": len(archive_groups),
        "direct_copy_count": len(direct_entries),
        "source_object_count": len(manifest.entries),
        "skipped_object_count": len(skipped_objects),
        "destination_archive_keys": archive_keys,
        "destination_keys": all_destination_keys,
        "archive_groups": json_list(archive_groups),
        "direct_entries": json_list(direct_entries),
        "skipped_objects": json_list(skipped_objects),
        "entries": json_list([_manifest_entry_payload(entry) for entry in manifest.entries]),
    }

    snapshots: dict[str, JsonValue] = {
        "before_archive": before_snapshot,
        "after_archive": after_archive_snapshot,
    }
    summary: dict[str, JsonValue] = {
        "status": "ok" if archive_payload.get("status") == "ok" else "error",
        **route_summary_payload(settings),
        "log_file": str(log_file),
        "run_started_at_utc": started.isoformat(),
        "health": health,
        "archive_manifest": archive_manifest,
        "archive_result": archive_payload,
        "snapshots": snapshots,
    }
    emit("Demo summary JSON follows on the next line.")
    emit(json.dumps(summary, sort_keys=True))
    return summary


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
