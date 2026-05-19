"""Shared archive payload shaping helpers."""

from __future__ import annotations

from collections.abc import Iterable, Sized
from typing import cast

from s3_archiver_core.archive import ArchivePhaseResult
from s3_archiver_core.payload_utils import JsonScalar as JsonScalar
from s3_archiver_core.payload_utils import (
    JsonValue,
    attr,
    date_text,
    datetime_text,
    int_or_none,
    json_list,
    object_list,
    string_or_none,
)


def phase_status(result: ArchivePhaseResult) -> str:
    """Return the structured status string for one archive phase."""

    return "error" if not result.ok else "skipped" if result.skipped else "ok"


def manifest_target_day(manifest: object) -> str:
    """Return the manifest target day as an ISO date string."""

    value = attr(manifest, "target_day")
    return date_text(value) if value is not None else ""


def archive_group_payloads(manifest: object) -> list[dict[str, JsonValue]]:
    """Return archive group payloads."""

    return [archive_group_payload(g) for g in object_list(attr(manifest, "archive_groups"))]


def direct_entry_payloads(manifest: object) -> list[dict[str, JsonValue]]:
    """Return direct-copy destination payloads for flat manifest entries."""

    return [
        direct_entry_payload(e)
        for e in object_list(attr(manifest, "entries"))
        if string_or_none(attr(e, "copy_mode")) == "direct"
    ]


def archive_group_payload(group: object) -> dict[str, JsonValue]:
    """Return one archive group payload."""

    entries = object_list(attr(group, "entries"))
    count = attr(group, "source_object_count")
    return {
        **_route_identity_fields(group, entries),
        "target_day": date_text(attr(group, "target_day")),
        "archive_root": string_or_none(attr(group, "archive_root")),
        "destination_archive_key": _group_archive_key(group, entries),
        "source_object_count": count if isinstance(count, int) else len(entries),
        "skipped_object_count": 0,
        "source_objects": json_list([entry_reference_payload(e) for e in entries]),
    }


def direct_entry_payload(entry: object) -> dict[str, JsonValue]:
    """Build a destination payload for one direct-copy manifest entry."""

    target_day = attr(entry, "target_day")
    return {
        **_route_identity_fields(entry),
        "target_day": date_text(target_day) if target_day is not None else "",
        "archive_root": string_or_none(attr(entry, "archive_root")),
        "destination_key": string_or_none(attr(entry, "destination_key")),
        "source_object_count": 1,
        "skipped_object_count": 0,
        "source_objects": json_list([entry_reference_payload(entry)]),
    }


def _route_identity_fields(
    obj: object, entries: list[object] | None = None
) -> dict[str, JsonValue]:
    """Return the route/bucket/identity fields shared by group and direct entry payloads."""

    fallback = entries or []
    return {
        "route_name": string_or_none(attr(obj, "route_name")),
        "parser_kind": string_or_none(attr(obj, "parser_kind")),
        "copy_mode": string_or_none(attr(obj, "copy_mode")),
        "source_bucket": string_or_none(attr(obj, "source_bucket")),
        "source_identity": string_or_none(attr(obj, "source_identity"))
        or _entry_value(fallback, "source_identity"),
        "destination_bucket": string_or_none(attr(obj, "destination_bucket")),
        "destination_identity": string_or_none(attr(obj, "destination_identity"))
        or _entry_value(fallback, "destination_identity"),
    }


def skipped_object_payloads(manifest: object) -> list[dict[str, JsonValue]]:
    """Return skipped source object payloads."""

    skipped = attr(manifest, "skipped_objects")
    return (
        [] if skipped is None else [skipped_object_payload(item) for item in object_list(skipped)]
    )


def skipped_object_payload(item: object) -> dict[str, JsonValue]:
    """Return one skipped object payload."""

    target_day = attr(item, "target_day")
    return {
        "key": string_or_none(attr(item, "key")),
        "reason": string_or_none(attr(item, "reason")),
        "route_name": string_or_none(attr(item, "route_name")),
        "parser_kind": string_or_none(attr(item, "parser_kind")),
        "copy_mode": string_or_none(attr(item, "copy_mode")),
        "size": int_or_none(attr(item, "size")),
        "last_modified": datetime_text(attr(item, "last_modified")),
        "version_id": string_or_none(attr(item, "version_id")),
        "selected_timestamp": datetime_text(attr(item, "selected_timestamp")),
        "timestamp_source": string_or_none(attr(item, "timestamp_source")),
        "source_bucket": string_or_none(attr(item, "source_bucket")),
        "destination_bucket": string_or_none(attr(item, "destination_bucket")),
        "source_identity": string_or_none(attr(item, "source_identity")),
        "destination_identity": string_or_none(attr(item, "destination_identity")),
        "target_day": None if target_day is None else date_text(target_day),
        "archive_root": string_or_none(attr(item, "archive_root")),
    }


def archive_manifest_payload(
    manifest: object,
    *,
    include_archive_days: bool = False,
    include_entries: bool = False,
    include_run_started_at_utc: bool = False,
    include_details: bool = True,
) -> dict[str, JsonValue]:
    """Return the shared JSON-ready archive manifest summary."""

    entries = attr(manifest, "entries")
    groups = attr(manifest, "archive_groups")
    skipped = attr(manifest, "skipped_objects")
    direct_count = _copy_mode_count(entries, "direct")
    payload: dict[str, JsonValue] = {
        "target_day": manifest_target_day(manifest),
        "archive_count": _sequence_count(groups),
        "direct_copy_count": direct_count,
        "source_object_count": _sequence_count(entries),
        "skipped_object_count": _sequence_count(skipped),
        "metrics": cast(
            JsonValue,
            {
                "manifest_storage": string_or_none(attr(manifest, "manifest_storage")),
                "source_byte_count": int_or_none(attr(manifest, "source_byte_count")) or 0,
            },
        ),
    }
    if include_details:
        archive_groups = archive_group_payloads(manifest)
        direct_entries = direct_entry_payloads(manifest)
        skipped_objects = skipped_object_payloads(manifest)
        payload.update(
            {
                "destination_archive_keys": [g["destination_archive_key"] for g in archive_groups],
                "destination_keys": [
                    *(g["destination_archive_key"] for g in archive_groups),
                    *(e["destination_key"] for e in direct_entries),
                ],
                "archive_groups": json_list(archive_groups),
                "direct_entries": json_list(direct_entries),
                "skipped_objects": json_list(skipped_objects),
            }
        )
    if include_archive_days:
        payload["archive_days"] = cast(JsonValue, _archive_days(groups))
    if include_entries:
        payload["entries"] = json_list([manifest_entry_payload(e) for e in object_list(entries)])
    if include_run_started_at_utc:
        payload["run_started_at_utc"] = datetime_text(attr(manifest, "run_started_at_utc"))
    return payload


def manifest_entry_payload(entry: object) -> dict[str, JsonValue]:
    """Return a JSON-ready payload for one archive manifest entry."""

    return {
        **entry_reference_payload(entry),
        "last_modified_utc": datetime_text(attr(entry, "last_modified")),
        "etag": string_or_none(attr(entry, "etag")),
    }


def entry_reference_payload(entry: object) -> dict[str, JsonValue]:
    """Return a compact source entry reference."""

    return {
        "key": string_or_none(attr(entry, "key")),
        "version_id": string_or_none(attr(entry, "version_id")),
        "size": int_or_none(attr(entry, "size")),
        "route_name": string_or_none(attr(entry, "route_name")),
        "parser_kind": string_or_none(attr(entry, "parser_kind")),
        "copy_mode": string_or_none(attr(entry, "copy_mode")),
        "source_bucket": string_or_none(attr(entry, "source_bucket")),
        "destination_bucket": string_or_none(attr(entry, "destination_bucket")),
        "destination_key": string_or_none(attr(entry, "destination_key")),
        "destination_archive_key": _entry_archive_key(entry),
        "source_identity": string_or_none(attr(entry, "source_identity")),
        "destination_identity": string_or_none(attr(entry, "destination_identity")),
    }


def _group_archive_key(group: object, entries: list[object]) -> str:
    value = attr(group, "destination_archive_key")
    if value is not None:
        return str(value)
    return _entry_destination_archive_key(entries[0]) if entries else ""


def _entry_destination_archive_key(entry: object) -> str:
    value = attr(entry, "destination_archive_key")
    return str(value if value is not None else attr(entry, "key") or "")


def _entry_archive_key(entry: object) -> str | None:
    if string_or_none(attr(entry, "copy_mode")) == "direct":
        return None
    return _entry_destination_archive_key(entry)


def _entry_value(entries: list[object], name: str) -> str | None:
    for entry in entries:
        value = string_or_none(attr(entry, name))
        if value is not None:
            return value
    return None


def _sequence_count(value: object | None) -> int:
    if value is None:
        return 0
    if isinstance(value, Sized) and not isinstance(value, str):
        return len(value)
    return len(object_list(value))


def _copy_mode_count(value: object | None, copy_mode: str) -> int:
    if value is None:
        return 0
    count_copy_mode = getattr(value, "count_copy_mode", None)
    if callable(count_copy_mode):
        counted = count_copy_mode(copy_mode)
        return counted if isinstance(counted, int) else 0
    return sum(
        1 for entry in object_list(value) if string_or_none(attr(entry, "copy_mode")) == copy_mode
    )


def _archive_days(value: object | None) -> list[str]:
    target_days = getattr(value, "target_days", None)
    if callable(target_days):
        raw_days = target_days()
        if isinstance(raw_days, Iterable) and not isinstance(raw_days, str):
            return [str(day) for day in raw_days]
    return sorted({date_text(attr(g, "target_day")) for g in object_list(value)})
