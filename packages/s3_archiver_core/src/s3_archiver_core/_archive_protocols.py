"""Private archive protocol definitions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Protocol

from s3_archiver_core.archive_transfer import TransferStrategy
from s3_archiver_core.s3 import S3ListedObject, S3ObjectProperties, VersioningState


class ArchiveBucket(Protocol):
    """S3 bucket operations required by the archive engine."""

    @property
    def bucket(self) -> str:
        """Return the bucket name."""
        ...

    def versioning_state(self) -> VersioningState:
        """Return bucket versioning state."""
        ...

    def list_source_objects(self, versioning_state: VersioningState) -> Iterable[S3ListedObject]:
        """List source objects."""
        ...

    def head_object(self, key: str, version_id: str | None = None) -> S3ObjectProperties | None:
        """Return object properties."""
        ...

    def content_sha256(self, key: str, version_id: str | None = None) -> str | None:
        """Return a SHA-256 digest for object content."""
        ...

    def copy_from(
        self,
        source: ArchiveBucket,
        source_bucket: str,
        source_key: str,
        source_version_id: str | None,
        properties: S3ObjectProperties,
        destination_key: str,
        destination_metadata: Mapping[str, str],
        strategy: TransferStrategy,
    ) -> None:
        """Copy a source object."""
        ...

    def delete_source(self, key: str, version_id: str | None) -> None:
        """Delete a source object."""
        ...


class ArchiveRunLock(Protocol):
    """Single-run lock boundary."""

    def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: timedelta) -> bool:
        """Try to acquire the lock for this run."""
        ...

    def release(self, *, run_id: str) -> None:
        """Release the lock owned by this run."""
        ...
