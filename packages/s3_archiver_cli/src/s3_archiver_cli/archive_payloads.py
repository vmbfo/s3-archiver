"""Shared archive payload shaping helpers."""

from __future__ import annotations

from typing import cast

from s3_archiver_core.archive import ArchivePhaseResult

from s3_archiver_cli.archive_payload_utils import (
    JsonScalar as JsonScalar,
)
from s3_archiver_cli.archive_payload_utils import (
    JsonValue,
    attr,
    count_from_attr,
    date_or_none,
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

    value = attr(manifest, "target_day", "target_date")
    if value is not None:
        return date_text(value)
    return ""


def archive_group_payloads(manifest: object) -> list[dict[str, JsonValue]]:
    """Return group payloads from either new group models or entry fallback data."""

    groups = attr(manifest, "archive_groups", "groups", "grouped_archive_entries")
    if groups is not None:
        return [archive_group_payload(group, manifest) for group in object_list(groups)]
    grouped_entries: dict[str, list[object]] = {}
    for entry in cast(tuple[object, ...], attr(manifest, "entries") or ()):
        grouped_entries.setdefault(entry_destination_archive_key(entry), []).append(entry)
    return [
        archive_group_payload_from_entries(
            destination_key,
            entries,
            manifest,
        )
        for destination_key, entries in sorted(grouped_entries.items())
    ]


def direct_entry_payloads(manifest: object) -> list[dict[str, JsonValue]]:
    """Return direct-copy destination payloads for flat manifest entries."""

    return [
        direct_entry_payload(entry)
        for entry in object_list(attr(manifest, "entries"))
        if string_or_none(attr(entry, "copy_mode")) == "direct"
    ]


def archive_group_payload(
    group: object,
    manifest: object,
) -> dict[str, JsonValue]:
    """Return one archive group payload."""

    _ = manifest
    entries = object_list(attr(group, "entries", "source_entries", "objects"))
    skipped = object_list(attr(group, "skipped_objects", "skipped_entries"))
    target_day = attr(group, "target_day", "target_date")
    route_name = string_or_none(attr(group, "route_name")) or entry_value(entries, "route_name")
    parser_kind = string_or_none(attr(group, "parser_kind")) or entry_value(entries, "parser_kind")
    payload: dict[str, JsonValue] = {
        "route_name": route_name,
        "parser_kind": parser_kind,
        "copy_mode": string_or_none(attr(group, "copy_mode")) or entry_value(entries, "copy_mode"),
        "source_bucket": string_or_none(attr(group, "source_bucket"))
        or entry_value(entries, "source_bucket"),
        "source_identity": string_or_none(attr(group, "source_identity"))
        or entry_value(entries, "source_identity"),
        "destination_bucket": string_or_none(attr(group, "destination_bucket"))
        or entry_value(entries, "destination_bucket"),
        "destination_identity": string_or_none(attr(group, "destination_identity"))
        or entry_value(entries, "destination_identity"),
        "target_day": date_text(target_day),
        "archive_root": string_or_none(attr(group, "archive_root", "root")),
        "destination_archive_key": group_destination_archive_key(group, entries),
        "source_object_count": count_from_attr(group, "source_object_count", entries),
        "skipped_object_count": count_from_attr(group, "skipped_object_count", skipped),
        "source_objects": json_list([entry_reference_payload(entry) for entry in entries]),
    }
    if skipped:
        payload["skipped_objects"] = json_list([skipped_object_payload(item) for item in skipped])
    return payload


def archive_group_payload_from_entries(
    destination_key: str,
    entries: list[object],
    manifest: object,
) -> dict[str, JsonValue]:
    """Build a group payload from legacy flat manifest entries."""

    _ = manifest
    payload: dict[str, JsonValue] = {
        "route_name": entry_value(entries, "route_name"),
        "parser_kind": entry_value(entries, "parser_kind"),
        "copy_mode": entry_value(entries, "copy_mode"),
        "source_bucket": entry_value(entries, "source_bucket"),
        "source_identity": entry_value(entries, "source_identity"),
        "destination_bucket": entry_value(entries, "destination_bucket"),
        "destination_identity": entry_value(entries, "destination_identity"),
        "target_day": entry_target_day(entries),
        "archive_root": string_or_none(attr(entries[0], "archive_root")) if entries else None,
        "destination_archive_key": destination_key,
        "source_object_count": len(entries),
        "skipped_object_count": 0,
        "source_objects": json_list([entry_reference_payload(entry) for entry in entries]),
    }
    return payload


def direct_entry_payload(entry: object) -> dict[str, JsonValue]:
    """Build a destination payload for one direct-copy manifest entry."""

    return {
        "route_name": string_or_none(attr(entry, "route_name")),
        "parser_kind": string_or_none(attr(entry, "parser_kind")),
        "copy_mode": string_or_none(attr(entry, "copy_mode")),
        "source_bucket": string_or_none(attr(entry, "source_bucket")),
        "source_identity": string_or_none(attr(entry, "source_identity")),
        "destination_bucket": string_or_none(attr(entry, "destination_bucket")),
        "destination_identity": string_or_none(attr(entry, "destination_identity")),
        "target_day": date_text(attr(entry, "target_day")) if attr(entry, "target_day") else "",
        "archive_root": string_or_none(attr(entry, "archive_root")),
        "destination_key": string_or_none(attr(entry, "destination_key")),
        "source_object_count": 1,
        "skipped_object_count": 0,
        "source_objects": json_list([entry_reference_payload(entry)]),
    }


def skipped_object_payloads(manifest: object) -> list[dict[str, JsonValue]]:
    """Return skipped source object payloads."""

    skipped = attr(manifest, "skipped_objects", "skipped_entries", "skipped")
    if skipped is None:
        return []
    return [skipped_object_payload(item) for item in object_list(skipped)]


def skipped_object_payload(item: object) -> dict[str, JsonValue]:
    """Return one skipped object payload."""

    return {
        "key": string_or_none(attr(item, "key", "source_key")),
        "reason": string_or_none(attr(item, "reason", "skip_reason")),
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
        "target_day": date_or_none(attr(item, "target_day", "target_date")),
        "archive_root": string_or_none(attr(item, "archive_root", "root")),
    }


def entry_target_day(entries: list[object]) -> str:
    """Return the first explicit target day carried by grouped entries."""

    for entry in entries:
        value = attr(entry, "target_day", "target_date")
        if value is not None:
            return date_text(value)
    return ""


def entry_reference_payload(entry: object) -> dict[str, JsonValue]:
    """Return a compact source entry reference."""

    return {
        "key": string_or_none(attr(entry, "key", "source_key")),
        "version_id": string_or_none(attr(entry, "version_id")),
        "size": int_or_none(attr(entry, "size")),
        "route_name": string_or_none(attr(entry, "route_name")),
        "parser_kind": string_or_none(attr(entry, "parser_kind")),
        "copy_mode": string_or_none(attr(entry, "copy_mode")),
        "source_bucket": string_or_none(attr(entry, "source_bucket")),
        "destination_bucket": string_or_none(attr(entry, "destination_bucket")),
        "destination_key": string_or_none(attr(entry, "destination_key")),
        "destination_archive_key": entry_archive_key_payload(entry),
        "source_identity": string_or_none(attr(entry, "source_identity")),
        "destination_identity": string_or_none(attr(entry, "destination_identity")),
    }


def destination_archive_keys(groups: list[dict[str, JsonValue]]) -> list[JsonValue]:
    """Return destination archive keys from group payloads."""

    return [group["destination_archive_key"] for group in groups]


def destination_keys(
    groups: list[dict[str, JsonValue]],
    direct_entries: list[dict[str, JsonValue]],
) -> list[JsonValue]:
    """Return all destination keys, including direct-copy objects."""

    return [
        *destination_archive_keys(groups),
        *(entry["destination_key"] for entry in direct_entries),
    ]


def group_destination_archive_key(group: object, entries: list[object]) -> str:
    """Return a group's destination archive key."""

    value = attr(group, "destination_archive_key", "archive_key", "key")
    if value is not None:
        return str(value)
    keys = attr(group, "destination_archive_keys", "archive_keys")
    if keys is not None:
        first = next(iter(object_list(keys)), None)
        if first is not None:
            return str(first)
    return entry_destination_archive_key(entries[0]) if entries else ""


def entry_destination_archive_key(entry: object) -> str:
    """Return the destination archive key for a source entry."""

    value = attr(entry, "destination_archive_key", "archive_key")
    return str(value if value is not None else attr(entry, "key", "source_key") or "")


def entry_archive_key_payload(entry: object) -> str | None:
    """Return archive-key payload data only for archive copy modes."""

    if string_or_none(attr(entry, "copy_mode")) == "direct":
        return None
    return entry_destination_archive_key(entry)


def entry_value(entries: list[object], name: str) -> str | None:
    """Return a string payload value from the first entry that carries it."""

    for entry in entries:
        value = string_or_none(attr(entry, name))
        if value is not None:
            return value
    return None
