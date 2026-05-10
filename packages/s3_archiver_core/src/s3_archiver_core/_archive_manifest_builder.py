from __future__ import annotations

from datetime import date, datetime

from s3_archiver_core._archive_manifest_groups import group_entries
from s3_archiver_core._archive_manifest_models import (
    ArchiveGroup,
    ArchiveManifest,
    CopyMode,
    DestinationLocator,
    ManifestEntry,
    ParserKind,
    ParserSelector,
    SkippedObject,
    SourceLister,
)
from s3_archiver_core._archive_manifest_paths import (
    as_utc,
    join_key,
    normalize_prefix,
    relative_key,
    storage_identity,
)
from s3_archiver_core._archive_manifest_selection import select_object
from s3_archiver_core.parsers.filename_timestamp import (
    archive_root_for_key,
    destination_archive_key,
)
from s3_archiver_core.parsers.results import SkippedObject as ParserSkippedObject
from s3_archiver_core.parsers.results import TimestampSource
from s3_archiver_core.s3 import S3ListedObject, VersioningState


def build_archive_manifest(
    source: SourceLister,
    *,
    run_started_at_utc: datetime,
    versioning_state: VersioningState,
    parser_kind: ParserKind,
    copy_mode: CopyMode,
    route_name: str = "default",
    source_path: str = "",
    destination: DestinationLocator | None = None,
    destination_path: str = "",
    parser: ParserSelector | None = None,
    source_identity: object | None = None,
    destination_identity: object | None = None,
) -> ArchiveManifest:
    """Build an archive manifest from source object keys."""

    run_started = as_utc(run_started_at_utc)
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
        selected = select_object(parser_kind, parser, listed, source_path)
        if selected is None:
            skipped.append(
                _skipped_object(
                    listed.key,
                    "no reliable key timestamp",
                    listed=listed,
                    route_name=route_name,
                    parser_kind=parser_kind,
                    copy_mode=copy_mode,
                    source_bucket=source.bucket,
                    source_path=source_path,
                    destination_bucket=destination_bucket,
                    destination_path=destination_path,
                    source_identity=source_identity,
                    destination_identity=destination_identity,
                )
            )
            continue
        if isinstance(selected, SkippedObject | ParserSkippedObject):
            skipped.append(
                _skipped_object(
                    listed.key,
                    selected.reason,
                    listed=listed,
                    route_name=route_name,
                    parser_kind=parser_kind,
                    copy_mode=copy_mode,
                    source_bucket=source.bucket,
                    source_path=source_path,
                    destination_bucket=destination_bucket,
                    destination_path=destination_path,
                    source_identity=source_identity,
                    destination_identity=destination_identity,
                )
            )
            continue
        timestamp = as_utc(selected.timestamp)
        if timestamp > run_started:
            skipped.append(
                _skipped_object(
                    listed.key,
                    "parser timestamp after run start",
                    listed=listed,
                    route_name=route_name,
                    parser_kind=parser_kind,
                    copy_mode=copy_mode,
                    source_bucket=source.bucket,
                    selected_timestamp=timestamp,
                    timestamp_source=selected.timestamp_source,
                    target_day=timestamp.date(),
                    archive_root=selected.archive_root,
                    source_path=source_path,
                    destination_bucket=destination_bucket,
                    destination_path=destination_path,
                    source_identity=source_identity,
                    destination_identity=destination_identity,
                )
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
        entry_tuple,
        None,
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
                group_entries(entries, route_name, root, target_day, destination_key),
                key=lambda item: item.key,
            )
        )
        first = grouped[0]
        groups.append(
            ArchiveGroup(
                target_day,
                root,
                destination_key,
                grouped,
                route_name,
                first.parser_kind,
                first.copy_mode,
                first.source_bucket,
                first.source_identity,
                destination_bucket,
                first.destination_identity,
            )
        )
    return tuple(groups)


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
        else archive_root_for_key(relative_key(listed.key, source_path))
    )
    destination_key = (
        join_key(destination_path, listed.key)
        if copy_mode == "direct"
        else join_key(destination_path, destination_archive_key(root, target_day))
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


def _skipped_object(
    key: str,
    reason: str,
    *,
    listed: S3ListedObject,
    route_name: str,
    parser_kind: ParserKind,
    copy_mode: CopyMode,
    source_bucket: str,
    source_path: str,
    destination_bucket: str,
    destination_path: str,
    source_identity: object | None,
    destination_identity: object | None,
    selected_timestamp: datetime | None = None,
    timestamp_source: TimestampSource | None = None,
    target_day: date | None = None,
    archive_root: str | None = None,
) -> SkippedObject:
    return SkippedObject(
        key,
        reason,
        route_name,
        parser_kind,
        copy_mode,
        listed.size,
        listed.last_modified,
        listed.etag,
        listed.version_id,
        selected_timestamp,
        timestamp_source,
        target_day,
        archive_root or "",
        source_bucket,
        source_path,
        destination_bucket,
        destination_path,
        source_identity,
        destination_identity,
    )
