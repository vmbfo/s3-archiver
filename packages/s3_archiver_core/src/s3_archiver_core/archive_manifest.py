"""Manifest construction for archive runs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from s3_archiver_core.s3 import S3ListedObject, VersioningState

FilterMode = Literal["none", "whitelist", "blacklist"]


class SourceLister(Protocol):
    """Source bucket listing boundary used by manifest construction."""

    bucket: str

    def list_source_objects(self, versioning_state: VersioningState) -> Iterable[S3ListedObject]:
        """Yield source objects for the given versioning state."""
        ...


@dataclass(frozen=True, slots=True)
class SourcePathFilter:
    """Source-key prefix filter."""

    mode: FilterMode = "none"
    prefixes: tuple[str, ...] = ()

    def includes(self, key: str) -> bool:
        """Return whether a source key is allowed into the archive manifest."""

        if self.mode == "none":
            return True
        matched = any(key.startswith(prefix) for prefix in self.prefixes)
        if self.mode == "whitelist":
            return matched
        return not matched


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """A source object pinned for one archive run."""

    source_bucket: str
    key: str
    size: int
    last_modified: datetime
    etag: str | None
    version_id: str | None
    object: S3ListedObject


@dataclass(frozen=True, slots=True)
class ArchiveManifest:
    """Frozen object set selected for one archive run."""

    run_started_at_utc: datetime
    retention_cutoff_utc: datetime
    entries: tuple[ManifestEntry, ...]


def build_archive_manifest(
    source: SourceLister,
    *,
    run_started_at_utc: datetime,
    retention_days: int,
    versioning_state: VersioningState,
    source_filter: SourcePathFilter,
) -> ArchiveManifest:
    """Build the manifest using the frozen run timestamp and source filters."""

    cutoff = run_started_at_utc - timedelta(days=retention_days)
    entries = tuple(
        _entry(source.bucket, listed)
        for listed in source.list_source_objects(versioning_state)
        if source_filter.includes(listed.key) and listed.last_modified < cutoff
    )
    return ArchiveManifest(run_started_at_utc, cutoff, entries)


def _entry(source_bucket: str, listed: S3ListedObject) -> ManifestEntry:
    return ManifestEntry(
        source_bucket=source_bucket,
        key=listed.key,
        size=listed.size,
        last_modified=listed.last_modified,
        etag=listed.etag,
        version_id=listed.version_id,
        object=listed,
    )
