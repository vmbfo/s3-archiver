from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol

from s3_archiver_core.archive_timestamp import TimestampSource
from s3_archiver_core.s3 import S3ListedObject, VersioningState

CopyMode = Literal["direct", "daily_tar_gz"]
FilterMode = Literal["none", "whitelist", "blacklist"]
ParserKind = Literal["direct", "filename_timestamp", "folder_timestamp"]


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
class SourcePathFilter:
    """Prefix filter applied while selecting archive candidates."""

    mode: FilterMode = "none"
    prefixes: tuple[str, ...] = ()

    def includes(self, key: str) -> bool:
        """Return whether a key is included by this filter."""
        if self.mode == "none":
            return True
        matched = any(key.startswith(prefix) for prefix in self.prefixes)
        if self.mode == "whitelist":
            return matched
        return not matched


DEFAULT_SOURCE_FILTER = SourcePathFilter()


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
    destination_bucket: str = ""
    destination_identity: object | None = None


@dataclass(frozen=True, slots=True)
class SkippedObject:
    """Source object skipped while building a manifest."""

    key: str
    reason: str
    route_name: str = "default"


@dataclass(frozen=True, slots=True)
class ArchiveManifest:
    """Complete archive plan for one run."""

    run_started_at_utc: datetime
    retention_cutoff_utc: datetime
    entries: tuple[ManifestEntry, ...]
    target_day: date | None = None
    archive_groups: tuple[ArchiveGroup, ...] = ()
    skipped_objects: tuple[SkippedObject, ...] = ()


ParserSelector = Callable[[S3ListedObject], SelectedObject | SkippedObject | None]


@dataclass(frozen=True, slots=True)
class ArchiveManifestRoute:
    """One route used to build a global archive manifest."""

    name: str
    source: SourceLister
    destination: DestinationLocator
    source_path: str = ""
    destination_path: str = ""
    parser_kind: ParserKind = "filename_timestamp"
    copy_mode: CopyMode = "daily_tar_gz"
    parser: ParserSelector | None = None
    versioning_state: VersioningState | None = None
    source_identity: object | None = None
    destination_identity: object | None = None
