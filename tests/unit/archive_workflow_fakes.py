"""Shared fakes for archive workflow unit tests."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from s3_archiver_core.archive_transfer import TransferStrategy
from s3_archiver_core.s3 import S3ListedObject, S3ObjectProperties, VersioningState


def object_properties(
    *,
    size: int = 10,
    metadata: Mapping[str, str] | None = None,
    tags: Mapping[str, str] | None = None,
) -> S3ObjectProperties:
    """Build portable S3 object properties for tests."""

    return S3ObjectProperties(
        size=size,
        etag='"etag"',
        content_type="text/plain",
        content_encoding="gzip",
        content_language="en",
        content_disposition="inline",
        cache_control="max-age=60",
        expires=datetime(2024, 1, 1, tzinfo=UTC),
        metadata=metadata or {"owner": "archive"},
        tags=tags or {"kind": "source"},
    )


def listed_object(key: str, age_days: int, version_id: str | None = "v1") -> S3ListedObject:
    """Build a listed source object relative to the fixed archive clock."""

    size = 10
    return S3ListedObject(
        key=key,
        size=size,
        last_modified=datetime(2024, 4, 20, tzinfo=UTC) - timedelta(days=age_days),
        etag='"etag"',
        version_id=version_id,
        properties=object_properties(size=size),
    )


class FakeBucket:
    """In-memory archive bucket test double."""

    bucket: str
    copied: list[str]
    deleted: list[tuple[str, str | None]]
    fail_copy: bool
    _objects: dict[str, S3ListedObject]
    _destination: dict[str, S3ObjectProperties]
    _versioning_state: VersioningState

    def __init__(
        self,
        bucket: str,
        objects: Iterable[S3ListedObject] = (),
        destination: Mapping[str, S3ObjectProperties] | None = None,
        versioning_state: VersioningState = "Enabled",
    ) -> None:
        self.bucket = bucket
        self.copied = []
        self.deleted = []
        self.fail_copy = False
        self._objects = {item.key: item for item in objects}
        self._destination = dict(destination or {})
        self._versioning_state = versioning_state

    def versioning_state(self) -> VersioningState:
        return self._versioning_state

    def list_source_objects(self, versioning_state: VersioningState) -> Iterable[S3ListedObject]:
        assert versioning_state == self._versioning_state
        return tuple(self._objects.values())

    def head_object(self, key: str, version_id: str | None = None) -> S3ObjectProperties | None:
        _ = version_id
        return self._destination.get(key)

    def copy_from(
        self,
        source: object,
        source_bucket: str,
        source_key: str,
        source_version_id: str | None,
        properties: S3ObjectProperties,
        destination_key: str,
        destination_metadata: Mapping[str, str],
        strategy: TransferStrategy,
    ) -> None:
        assert isinstance(source, FakeBucket)
        assert source.bucket == source_bucket
        _ = (source_version_id, strategy)
        if self.fail_copy:
            raise RuntimeError("copy failed")
        self.copied.append(source_key)
        self._destination[destination_key] = replace(properties, metadata=destination_metadata)

    def delete_source(self, key: str, version_id: str | None) -> None:
        self.deleted.append((key, version_id))
