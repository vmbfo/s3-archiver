"""Snapshot payload helpers for the visual demo."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from s3_archiver_core.archive import ArchiveRoute
from s3_archiver_core.archive_fingerprint import fingerprint_from_metadata
from s3_archiver_core.archive_manifest import ArchiveManifest, ManifestEntry
from s3_archiver_core.s3 import S3ListedObject

from s3_archiver_cli.archive_payload_utils import JsonValue, json_list


def snapshot_payload(
    routes: tuple[ArchiveRoute, ...],
    *,
    eligible_keys: set[tuple[str, str, str | None]],
    planned_destinations: dict[tuple[str, str, str | None], str],
) -> dict[str, JsonValue]:
    """Build the visual-demo object snapshot for the configured routes."""

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


def manifest_entry_payload(entry: ManifestEntry) -> dict[str, JsonValue]:
    """Return a JSON-ready payload for one archive manifest entry."""

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
        "parser_kind": entry.parser_kind,
        "copy_mode": entry.copy_mode,
        "source_identity": str(entry.source_identity),
        "destination_identity": str(entry.destination_identity),
    }


def manifest_key_set(manifest: ArchiveManifest) -> set[tuple[str, str, str | None]]:
    """Return manifest entry identities keyed by route, source key, and version."""

    return {(entry.route_name, entry.key, entry.version_id) for entry in manifest.entries}


def manifest_destination_key_map(
    manifest: ArchiveManifest,
) -> dict[tuple[str, str, str | None], str]:
    """Return planned destination keys keyed by route, source key, and version."""

    return {
        (entry.route_name, entry.key, entry.version_id): entry.destination_key
        for entry in manifest.entries
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
