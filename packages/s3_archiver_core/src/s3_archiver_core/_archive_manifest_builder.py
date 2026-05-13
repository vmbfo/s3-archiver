from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from s3_archiver_core._archive_identity import stable_identity_value
from s3_archiver_core._archive_manifest_models import (
    ArchiveGroup,
    ArchiveManifest,
    CopyMode,
    DestinationLocator,
    ManifestEntry,
    ParserKind,
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
    source_identity: object | None = None,
    destination_identity: object | None = None,
) -> ArchiveManifest:
    """Build an archive manifest from source object keys."""

    context = _ManifestBuildContext(
        source=source,
        destination=destination,
        route_name=route_name,
        parser_kind=parser_kind,
        copy_mode=copy_mode,
        source_path=normalize_prefix(source_path),
        destination_path=normalize_prefix(destination_path),
        source_identity=source_identity or storage_identity(source),
        destination_identity=destination_identity or storage_identity(destination),
    )
    run_started = as_utc(run_started_at_utc)
    entries: list[ManifestEntry] = []
    skipped: list[SkippedObject] = []
    for listed in source.list_source_objects(versioning_state, prefix=context.source_path):
        if context.source_path and not listed.key.startswith(context.source_path):
            continue
        selected = select_object(parser_kind, listed, context.source_path)
        if isinstance(selected, SkippedObject | ParserSkippedObject):
            skipped.append(context.skipped_object(listed, selected.reason))
            continue
        timestamp = as_utc(selected.timestamp)
        if timestamp > run_started:
            skipped.append(
                context.skipped_object(
                    listed,
                    "parser timestamp after run start",
                    selected_timestamp=timestamp,
                    timestamp_source=selected.timestamp_source,
                    target_day=timestamp.date(),
                    archive_root=selected.archive_root,
                )
            )
            continue
        entries.append(
            context.entry(
                listed,
                timestamp,
                selected.timestamp_source,
                timestamp.date(),
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
    """Group daily tar entries by route, root, target day, key, and destination."""

    grouped_entries: dict[_ArchiveGroupKey, list[ManifestEntry]] = {}
    for entry in entries:
        if entry.copy_mode != "daily_tar_gz" or entry.target_day is None:
            continue
        key = (
            entry.route_name,
            entry.archive_root,
            entry.target_day,
            entry.destination_bucket,
            entry.destination_archive_key,
            _stable_sort_value(entry.destination_identity),
        )
        grouped_entries.setdefault(key, []).append(entry)

    groups: list[ArchiveGroup] = []
    for key in sorted(grouped_entries):
        grouped = tuple(sorted(grouped_entries[key], key=lambda item: item.key))
        first = grouped[0]
        if first.target_day is None:  # pragma: no cover - guarded before grouping
            raise RuntimeError("daily archive group missing target day")
        groups.append(
            ArchiveGroup(
                target_day=first.target_day,
                archive_root=first.archive_root,
                destination_archive_key=first.destination_archive_key,
                entries=grouped,
                route_name=first.route_name,
                parser_kind=first.parser_kind,
                copy_mode=first.copy_mode,
                source_bucket=first.source_bucket,
                source_identity=first.source_identity,
                destination_bucket=first.destination_bucket,
                destination_identity=first.destination_identity,
            )
        )
    return tuple(groups)


type _ArchiveGroupKey = tuple[str, str, date, str, str, str]


@dataclass(frozen=True, slots=True)
class _ManifestBuildContext:
    source: SourceLister
    destination: DestinationLocator | None
    route_name: str
    parser_kind: ParserKind
    copy_mode: CopyMode
    source_path: str
    destination_path: str
    source_identity: object | None
    destination_identity: object | None

    @property
    def destination_bucket(self) -> str:
        return "" if self.destination is None else self.destination.bucket

    def entry(
        self,
        listed: S3ListedObject,
        selected_timestamp: datetime,
        timestamp_source: TimestampSource,
        target_day: date,
        *,
        archive_root: str | None,
    ) -> ManifestEntry:
        root = (
            archive_root
            if archive_root is not None
            else archive_root_for_key(relative_key(listed.key, self.source_path))
        )
        destination_key = (
            join_key(self.destination_path, listed.key)
            if self.copy_mode == "direct"
            else join_key(self.destination_path, destination_archive_key(root, target_day))
        )
        return ManifestEntry(
            source_bucket=self.source.bucket,
            key=listed.key,
            size=listed.size,
            last_modified=listed.last_modified,
            etag=listed.etag,
            version_id=listed.version_id,
            object=listed,
            selected_timestamp=selected_timestamp,
            timestamp_source=timestamp_source,
            target_day=target_day,
            archive_root=root,
            destination_archive_key=destination_key,
            route_name=self.route_name,
            parser_kind=self.parser_kind,
            copy_mode=self.copy_mode,
            source_path=self.source_path,
            destination_bucket=self.destination_bucket,
            destination_path=self.destination_path,
            destination_key=destination_key,
            source_identity=self.source_identity,
            destination_identity=self.destination_identity,
        )

    def skipped_object(
        self,
        listed: S3ListedObject,
        reason: str,
        *,
        selected_timestamp: datetime | None = None,
        timestamp_source: TimestampSource | None = None,
        target_day: date | None = None,
        archive_root: str | None = None,
    ) -> SkippedObject:
        return SkippedObject(
            key=listed.key,
            reason=reason,
            route_name=self.route_name,
            parser_kind=self.parser_kind,
            copy_mode=self.copy_mode,
            size=listed.size,
            last_modified=listed.last_modified,
            etag=listed.etag,
            version_id=listed.version_id,
            selected_timestamp=selected_timestamp,
            timestamp_source=timestamp_source,
            target_day=target_day,
            archive_root=archive_root or "",
            source_bucket=self.source.bucket,
            source_path=self.source_path,
            destination_bucket=self.destination_bucket,
            destination_path=self.destination_path,
            source_identity=self.source_identity,
            destination_identity=self.destination_identity,
        )


def _stable_sort_value(value: object) -> str:
    return repr(stable_identity_value(value))
