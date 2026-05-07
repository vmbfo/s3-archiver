from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import KW_ONLY, dataclass
from datetime import date, datetime
from typing import Literal, Protocol

from s3_archiver_core.parsers.protocol import ParserContext
from s3_archiver_core.parsers.results import SkippedObject as ParserSkippedObject
from s3_archiver_core.parsers.results import TimestampSource
from s3_archiver_core.s3 import S3ListedObject, VersioningState

CopyMode = Literal["direct", "daily_tar_gz"]
ParserKind = str


class SourceLister(Protocol):
    """Source bucket interface used to build archive manifests."""

    @property
    def bucket(self) -> str:
        """Return the source bucket name."""
        ...

    def versioning_state(self) -> VersioningState:
        """Return the source bucket versioning state."""
        ...

    def list_source_objects(self, versioning_state: VersioningState) -> Iterable[S3ListedObject]:
        """List source objects for the given versioning state."""
        ...


class DestinationLocator(Protocol):
    """Destination bucket identity used while constructing manifests."""

    @property
    def bucket(self) -> str:
        """Return the destination bucket name."""
        ...


@dataclass(frozen=True, slots=True)
class SelectedObject:
    """Parser-selected source object eligibility details."""

    timestamp: datetime
    timestamp_source: TimestampSource
    archive_root: str | None = None


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One source object selected for archive execution."""

    source_bucket: str
    key: str
    size: int
    last_modified: datetime
    etag: str | None
    version_id: str | None
    object: S3ListedObject
    selected_timestamp: datetime | None = None
    timestamp_source: TimestampSource | None = None
    target_day: date | None = None
    archive_root: str = ""
    destination_archive_key: str = ""
    route_name: str = "default"
    parser_kind: ParserKind = "filename_timestamp"
    copy_mode: CopyMode = "daily_tar_gz"
    source_path: str = ""
    destination_bucket: str = ""
    destination_path: str = ""
    destination_key: str = ""
    source_identity: object | None = None
    destination_identity: object | None = None


@dataclass(frozen=True, slots=True)
class ArchiveGroup:
    """Source objects grouped into one destination archive."""

    target_day: date
    archive_root: str
    destination_archive_key: str
    entries: tuple[ManifestEntry, ...]
    route_name: str = "default"
    parser_kind: ParserKind = "filename_timestamp"
    copy_mode: CopyMode = "daily_tar_gz"
    source_bucket: str = ""
    source_identity: object | None = None
    destination_bucket: str = ""
    destination_identity: object | None = None


@dataclass(frozen=True, slots=True)
class SkippedObject:
    """Source object skipped while building a manifest."""

    key: str
    reason: str
    route_name: str = "default"
    parser_kind: ParserKind = "filename_timestamp"
    copy_mode: CopyMode = "daily_tar_gz"
    size: int | None = None
    last_modified: datetime | None = None
    etag: str | None = None
    version_id: str | None = None
    selected_timestamp: datetime | None = None
    timestamp_source: TimestampSource | None = None
    target_day: date | None = None
    archive_root: str = ""
    source_bucket: str = ""
    source_path: str = ""
    destination_bucket: str = ""
    destination_path: str = ""
    source_identity: object | None = None
    destination_identity: object | None = None


@dataclass(frozen=True, slots=True)
class ArchiveManifest:
    """Complete archive plan for one run."""

    run_started_at_utc: datetime
    entries: tuple[ManifestEntry, ...]
    target_day: date | None = None
    archive_groups: tuple[ArchiveGroup, ...] = ()
    skipped_objects: tuple[SkippedObject, ...] = ()


ParserResult = SelectedObject | SkippedObject | ParserSkippedObject | None
ParserSelector = (
    Callable[[S3ListedObject], ParserResult]
    | Callable[[S3ListedObject, ParserContext], ParserResult]
)


@dataclass(frozen=True, slots=True)
class ArchiveManifestRoute:
    """One route used to build a global archive manifest."""

    name: str
    source: SourceLister
    destination: DestinationLocator
    _: KW_ONLY
    parser_kind: ParserKind
    copy_mode: CopyMode
    source_path: str = ""
    destination_path: str = ""
    parser: ParserSelector | None = None
    versioning_state: VersioningState | None = None
    source_identity: object | None = None
    destination_identity: object | None = None
