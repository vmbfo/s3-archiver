from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta

from s3_archiver_core._archive_manifest_models import (
    DEFAULT_SOURCE_FILTER,
    ArchiveGroup,
    ArchiveManifest,
    CopyMode,
    DestinationLocator,
    ManifestEntry,
    ParserKind,
    ParserSelector,
    SelectedObject,
    SkippedObject,
    SourceLister,
    SourcePathFilter,
)
from s3_archiver_core.archive_timestamp import (
    TimestampSource,
    archive_root_for_key,
    destination_archive_key,
)
from s3_archiver_core.parsers.kinds import ParserKind as RegisteredParserKind
from s3_archiver_core.parsers.registry import parser_for_kind
from s3_archiver_core.parsers.results import SkippedObject as ParserSkippedObject
from s3_archiver_core.s3 import S3ListedObject, VersioningState


def build_archive_manifest(
    source: SourceLister,
    *,
    run_started_at_utc: datetime,
    retention_days: int | None = None,
    versioning_state: VersioningState,
    source_filter: SourcePathFilter = DEFAULT_SOURCE_FILTER,
    route_name: str = "default",
    parser_kind: ParserKind = "filename_timestamp",
    copy_mode: CopyMode = "daily_tar_gz",
    source_path: str = "",
    destination: DestinationLocator | None = None,
    destination_path: str = "",
    parser: ParserSelector | None = None,
    source_identity: object | None = None,
    destination_identity: object | None = None,
) -> ArchiveManifest:
    """Build an archive manifest from source object keys."""

    run_started = as_utc(run_started_at_utc)
    target_day = (
        None if retention_days is None else run_started.date() - timedelta(days=retention_days)
    )
    cutoff = run_started if target_day is None else datetime.combine(target_day, time.min, UTC)
    entries: list[ManifestEntry] = []
    skipped: list[SkippedObject] = []
    source_identity = source_identity or storage_identity(source)
    destination_identity = destination_identity or storage_identity(destination)
    destination_bucket = "" if destination is None else destination.bucket
    source_path = normalize_prefix(source_path)
    destination_path = normalize_prefix(destination_path)
    for listed in source.list_source_objects(versioning_state):
        if source_path and not listed.key.startswith(source_path):
            continue
        if not source_filter.includes(listed.key):
            continue
        selected = _select_object(parser_kind, parser, listed, source_path)
        if selected is None:
            skipped.append(SkippedObject(listed.key, "no reliable key timestamp", route_name))
            continue
        if isinstance(selected, SkippedObject):
            skipped.append(SkippedObject(listed.key, selected.reason, route_name))
            continue
        timestamp = as_utc(selected.timestamp)
        if _outside_eligibility(timestamp, run_started, target_day):
            skipped.append(
                SkippedObject(listed.key, _eligibility_skip_reason(target_day), route_name)
            )
            continue
        entries.append(
            _entry(
                source.bucket,
                listed,
                timestamp,
                selected.timestamp_source,
                timestamp.date(),
                route_name=route_name,
                parser_kind=parser_kind,
                copy_mode=copy_mode,
                source_path=source_path,
                destination_bucket=destination_bucket,
                destination_path=destination_path,
                source_identity=source_identity,
                destination_identity=destination_identity,
                archive_root=selected.archive_root,
            )
        )
    entry_tuple = tuple(entries)
    return ArchiveManifest(
        run_started,
        cutoff,
        entry_tuple,
        target_day,
        archive_groups(entry_tuple),
        tuple(skipped),
    )


def archive_groups(entries: tuple[ManifestEntry, ...]) -> tuple[ArchiveGroup, ...]:
    """Group daily tar entries by route, archive root, and destination key."""

    group_keys = sorted(
        {
            (
                entry.route_name,
                entry.archive_root,
                entry.target_day,
                entry.destination_archive_key,
                entry.destination_bucket,
            )
            for entry in entries
            if entry.copy_mode == "daily_tar_gz" and entry.target_day is not None
        }
    )
    groups: list[ArchiveGroup] = []
    for route_name, root, target_day, destination_key, destination_bucket in group_keys:
        grouped = tuple(
            sorted(
                _group_entries(entries, route_name, root, target_day, destination_key),
                key=lambda item: item.key,
            )
        )
        groups.append(
            ArchiveGroup(
                target_day,
                root,
                destination_key,
                grouped,
                route_name,
                destination_bucket,
                grouped[0].destination_identity if grouped else None,
            )
        )
    return tuple(groups)


def normalize_prefix(value: str) -> str:
    stripped = value.strip("/")
    if stripped == "":
        return ""
    return f"{stripped}/"


def storage_identity(value: object | None) -> object | None:
    if value is None:
        return None
    storage_identity = getattr(value, "storage_identity", None)
    if callable(storage_identity):
        return storage_identity()
    return (type(value).__name__, getattr(value, "bucket", None))


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _entry(
    source_bucket: str,
    listed: S3ListedObject,
    selected_timestamp: datetime,
    timestamp_source: TimestampSource,
    target_day: date,
    *,
    route_name: str,
    parser_kind: ParserKind,
    copy_mode: CopyMode,
    source_path: str,
    destination_bucket: str,
    destination_path: str,
    source_identity: object | None,
    destination_identity: object | None,
    archive_root: str | None,
) -> ManifestEntry:
    root = (
        archive_root
        if archive_root is not None
        else archive_root_for_key(_relative_key(listed.key, source_path))
    )
    destination_key = (
        _join_key(destination_path, listed.key)
        if copy_mode == "direct"
        else _join_key(destination_path, destination_archive_key(root, target_day))
    )
    return ManifestEntry(
        source_bucket,
        listed.key,
        listed.size,
        listed.last_modified,
        listed.etag,
        listed.version_id,
        listed,
        selected_timestamp,
        timestamp_source,
        target_day,
        root,
        destination_key,
        route_name,
        parser_kind,
        copy_mode,
        source_path,
        destination_bucket,
        destination_path,
        destination_key,
        source_identity,
        destination_identity,
    )


def _group_entries(
    entries: tuple[ManifestEntry, ...],
    route_name: str,
    root: str,
    target_day: date,
    destination_key: str,
) -> Iterable[ManifestEntry]:
    return (
        entry
        for entry in entries
        if entry.route_name == route_name
        and entry.archive_root == root
        and entry.target_day == target_day
        and entry.destination_archive_key == destination_key
    )


def _select_object(
    parser_kind: ParserKind,
    parser: ParserSelector | None,
    listed: S3ListedObject,
    source_path: str,
) -> SelectedObject | SkippedObject | None:
    if parser is not None:
        return parser(listed)
    result = parser_for_kind(RegisteredParserKind(str(parser_kind))).parse(listed)
    if isinstance(result, ParserSkippedObject):
        return SkippedObject(listed.key, result.reason)
    return SelectedObject(
        as_utc(result.timestamp),
        result.timestamp_source,
        _relative_archive_root(result.archive_root, source_path),
    )


def _outside_eligibility(
    selected_timestamp: datetime, run_started: datetime, target_day: date | None
) -> bool:
    if selected_timestamp > run_started:
        return True
    return target_day is not None and selected_timestamp.date() > target_day


def _eligibility_skip_reason(target_day: date | None) -> str:
    if target_day is None:
        return "parser timestamp after run start"
    return "outside retention window"


def _relative_key(key: str, source_path: str) -> str:
    if source_path and key.startswith(source_path):
        return key[len(source_path) :]
    return key


def _relative_archive_root(archive_root: str, source_path: str) -> str:
    prefix = source_path.rstrip("/")
    if prefix == "":
        return archive_root
    if archive_root == prefix:
        return ""
    child_prefix = f"{prefix}/"
    if archive_root.startswith(child_prefix):
        return archive_root[len(child_prefix) :]
    return archive_root


def _join_key(prefix: str, key: str) -> str:
    normalized_prefix = normalize_prefix(prefix)
    stripped_key = key.lstrip("/")
    return f"{normalized_prefix}{stripped_key}" if normalized_prefix else stripped_key
