"""Human-readable archive demo output backed by real S3 state."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from s3_archiver_core.archive import ArchiveRoute
from s3_archiver_core.archive_fingerprint import fingerprint_from_metadata
from s3_archiver_core.archive_manifest import (
    ArchiveManifest,
    ArchiveManifestRoute,
    ManifestEntry,
    build_route_archive_manifest,
)
from s3_archiver_core.health import run_health_check
from s3_archiver_core.s3 import S3ListedObject, build_s3_client
from s3_archiver_core.settings import AppSettings
from s3_archiver_core.temp_files import prepare_runtime_temp_dir

from s3_archiver_cli import visual_demo_output as _output
from s3_archiver_cli._archive_routes import archive_routes_from_settings
from s3_archiver_cli.archive_payloads import (
    archive_group_payloads,
    json_list,
    manifest_target_day,
    skipped_object_payloads,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]
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
    manifest = build_route_archive_manifest(
        tuple(
            ArchiveManifestRoute(
                route.name,
                route.source,
                route.destination,
                route.source_path,
                route.destination_path,
                route.parser_kind,
                route.copy_mode,
                source_identity=route.source_identity,
                destination_identity=route.destination_identity,
            )
            for route in routes
        ),
        run_started_at_utc=started,
    )
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
    skipped_objects = skipped_object_payloads(manifest)
    archive_days = sorted({str(group["target_day"]) for group in archive_groups})
    archive_days_payload = [cast(JsonValue, day) for day in archive_days]
    archive_manifest: dict[str, JsonValue] = {
        "object_count": len(manifest.entries),
        "target_day": manifest_target_day(manifest),
        "archive_days": archive_days_payload,
        "archive_count": len(archive_groups),
        "source_object_count": len(manifest.entries),
        "skipped_object_count": len(skipped_objects),
        "destination_archive_keys": [group["destination_archive_key"] for group in archive_groups],
        "archive_groups": json_list(archive_groups),
        "skipped_objects": json_list(skipped_objects),
        "entries": json_list([_manifest_entry_payload(entry) for entry in manifest.entries]),
    }

    snapshots: dict[str, JsonValue] = {
        "before_archive": before_snapshot,
        "after_archive": after_archive_snapshot,
    }
    summary: dict[str, JsonValue] = {
        "status": "ok" if archive_payload.get("status") == "ok" else "error",
        "source_bucket": settings.source.bucket,
        "destination_bucket": settings.destination.bucket,
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


def _snapshot_payload(
    routes: tuple[ArchiveRoute, ...],
    *,
    eligible_keys: set[tuple[str, str, str | None]],
    planned_destinations: dict[tuple[str, str, str | None], str],
) -> dict[str, JsonValue]:
    route_snapshots = [
        _route_snapshot(route, eligible_keys, planned_destinations) for route in routes
    ]
    source_states = {str(snapshot["source_versioning_state"]) for snapshot in route_snapshots}
    destination_states = {
        str(snapshot["destination_versioning_state"]) for snapshot in route_snapshots
    }
    source_objects = [
        item
        for snapshot in route_snapshots
        for item in cast(list[dict[str, JsonValue]], snapshot["source_objects"])
    ]
    destination_objects = [
        item
        for snapshot in route_snapshots
        for item in cast(list[dict[str, JsonValue]], snapshot["destination_objects"])
    ]
    return {
        "source_versioning_state": (
            next(iter(source_states)) if len(source_states) == 1 else "mixed"
        ),
        "destination_versioning_state": (
            next(iter(destination_states)) if len(destination_states) == 1 else "mixed"
        ),
        "source_object_count": len(source_objects),
        "destination_object_count": len(destination_objects),
        "source_objects": json_list(source_objects),
        "destination_objects": json_list(destination_objects),
    }


def _route_snapshot(
    route: ArchiveRoute,
    eligible_keys: set[tuple[str, str, str | None]],
    planned_destinations: dict[tuple[str, str, str | None], str],
) -> dict[str, JsonValue]:
    source_state = route.source.versioning_state()
    destination_state = route.destination.versioning_state()
    source_objects = sorted(
        _objects_under_prefix(route.source.list_source_objects(source_state), route.source_path),
        key=_object_sort_key,
    )
    destination_objects = sorted(
        _objects_under_prefix(
            route.destination.list_source_objects(destination_state), route.destination_path
        ),
        key=_object_sort_key,
    )
    destination_keys = {item.key for item in destination_objects}
    return {
        "source_versioning_state": source_state,
        "destination_versioning_state": destination_state,
        "source_object_count": len(source_objects),
        "destination_object_count": len(destination_objects),
        "source_objects": json_list(
            [
                _listed_object_payload(
                    item,
                    route_name=route.name,
                    bucket=route.source.bucket,
                    eligible_for_follow_up=(route.name, item.key, item.version_id) in eligible_keys,
                    present_in_destination=_present_in_destination(
                        route,
                        item,
                        destination_keys,
                        planned_destinations,
                    ),
                )
                for item in source_objects
            ]
        ),
        "destination_objects": json_list(
            [
                _listed_object_payload(
                    item,
                    route_name=route.name,
                    bucket=route.destination.bucket,
                    eligible_for_follow_up=False,
                    present_in_destination=True,
                )
                for item in destination_objects
            ]
        ),
    }


def _listed_object_payload(
    item: S3ListedObject,
    *,
    route_name: str,
    bucket: str,
    eligible_for_follow_up: bool,
    present_in_destination: bool,
) -> dict[str, JsonValue]:
    fingerprint = fingerprint_from_metadata(item.properties.metadata)
    return {
        "route_name": route_name,
        "bucket": bucket,
        "key": item.key,
        "size": item.size,
        "last_modified_utc": item.last_modified.isoformat(),
        "etag": item.etag,
        "version_id": item.version_id,
        "source_last_modified": None if fingerprint is None else fingerprint.source_last_modified,
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
        "destination_bucket": entry.destination_bucket,
        "destination_key": entry.destination_key,
        "destination_archive_key": entry.destination_archive_key,
        "route_name": entry.route_name,
    }


def _manifest_key_set(manifest: ArchiveManifest) -> set[tuple[str, str, str | None]]:
    return {(entry.route_name, entry.key, entry.version_id) for entry in manifest.entries}


def _manifest_destination_key_map(
    manifest: ArchiveManifest,
) -> dict[tuple[str, str, str | None], str]:
    return {
        (entry.route_name, entry.key, entry.version_id): entry.destination_key
        for entry in manifest.entries
    }


def _present_in_destination(
    route: ArchiveRoute,
    item: S3ListedObject,
    destination_keys: set[str],
    planned_destinations: dict[tuple[str, str, str | None], str],
) -> bool:
    planned_key = planned_destinations.get((route.name, item.key, item.version_id))
    if planned_key is not None:
        return planned_key in destination_keys
    return item.key in destination_keys


def _objects_under_prefix(
    objects: Iterable[S3ListedObject],
    prefix: str,
) -> list[S3ListedObject]:
    stripped = prefix.strip("/")
    normalized = "" if stripped == "" else f"{stripped}/"
    return [item for item in objects if normalized == "" or item.key.startswith(normalized)]


def _object_sort_key(item: S3ListedObject) -> tuple[str, str]:
    return (item.key, "" if item.version_id is None else item.version_id)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
