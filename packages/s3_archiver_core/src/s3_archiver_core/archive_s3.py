"""Concrete S3 archive bucket adapter."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from botocore.exceptions import ClientError

from s3_archiver_core._archive_s3_protocols import ArchiveS3Client
from s3_archiver_core.archive_transfer import TransferStrategy
from s3_archiver_core.s3 import (
    S3_CHUNK_BYTES,
    S3Client,
    S3ListedObject,
    S3ObjectProperties,
    VersioningState,
    checksums_from_head_fields,
)
from s3_archiver_core.s3_transfer import copy_s3_object
from s3_archiver_core.temp_files import default_temp_dir


@dataclass(frozen=True, slots=True)
class S3ArchiveBucket:
    """Bucket-scoped S3 operations used by the archive engine."""

    client: ArchiveS3Client
    bucket: str
    temp_dir: Path = field(default_factory=default_temp_dir)

    def versioning_state(self) -> VersioningState:
        """Return the bucket versioning state."""

        response = self.client.get_bucket_versioning(Bucket=self.bucket)
        status = response.get("Status")
        if status == "Enabled":
            return "Enabled"
        if status == "Suspended":
            return "Suspended"
        return "Disabled"

    def list_source_objects(self, versioning_state: VersioningState) -> Iterator[S3ListedObject]:
        """Yield current source objects, excluding delete markers."""

        if versioning_state == "Disabled":
            yield from self._list_unversioned()
            return
        yield from self._list_versioned()

    def head_object(self, key: str, version_id: str | None = None) -> S3ObjectProperties | None:
        """Return object properties, or ``None`` when the object is absent."""

        kwargs = _versioned_kwargs(self.bucket, key, version_id)
        try:
            head = self.client.head_object(**(kwargs | {"ChecksumMode": "ENABLED"}))
        except ClientError as exc:
            if _is_not_found_error(exc):
                return None
            if not _supports_checksum_mode(exc):
                raise
            try:
                head = self.client.head_object(**kwargs)
            except ClientError as retry_exc:
                if _is_not_found_error(retry_exc):
                    return None
                raise
        tags = self.get_tags(key, version_id)
        return _properties_from_head(head, tags)

    def content_sha256(self, key: str, version_id: str | None = None) -> str | None:
        """Return a SHA-256 digest for object content, or ``None`` when absent."""

        try:
            response = self.client.get_object(**_versioned_kwargs(self.bucket, key, version_id))
        except ClientError as exc:
            if _is_not_found_error(exc):
                return None
            raise
        body = cast(ReadableBody, response["Body"])
        digest = hashlib.sha256()
        try:
            while chunk := body.read(S3_CHUNK_BYTES):
                digest.update(chunk)
        finally:
            body.close()
        return digest.hexdigest()

    def _source_properties(self, key: str, version_id: str | None) -> S3ObjectProperties:
        properties = self.head_object(key, version_id)
        if properties is None:
            raise FileNotFoundError(f"{key}: listed source object disappeared before metadata read")
        return properties

    def get_tags(self, key: str, version_id: str | None = None) -> Mapping[str, str]:
        """Return object tags as a plain string mapping."""

        response = self.client.get_object_tagging(**_versioned_kwargs(self.bucket, key, version_id))
        tag_set = response.get("TagSet", [])
        if not isinstance(tag_set, list):
            return {}
        raw_tags = cast(list[object], tag_set)
        tags: dict[str, str] = {}
        for raw_tag in raw_tags:
            if isinstance(raw_tag, dict):
                tag = cast(Mapping[object, object], raw_tag)
                tag_key = tag.get("Key")
                tag_value = tag.get("Value")
                if tag_key is not None and tag_value is not None:
                    tags[str(tag_key)] = str(tag_value)
        return tags

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
        """Copy one source object into this bucket while preserving portable properties."""

        if not isinstance(source, S3ArchiveBucket):
            raise TypeError("S3ArchiveBucket copy requires an S3ArchiveBucket source")
        copy_s3_object(
            cast(S3Client, self.client),
            cast(S3Client, source.client),
            source_bucket,
            source_key,
            source_version_id,
            properties,
            self.bucket,
            destination_key,
            destination_metadata,
            strategy,
            self.temp_dir,
        )

    def delete_source(self, key: str, version_id: str | None) -> None:
        """Delete an exact source version when available, otherwise delete by key."""

        _ = self.client.delete_object(**_versioned_kwargs(self.bucket, key, version_id))

    def _list_unversioned(self) -> Iterator[S3ListedObject]:
        continuation_token: str | None = None
        while True:
            kwargs: dict[str, object] = {"Bucket": self.bucket, "MaxKeys": 1000}
            if continuation_token is not None:
                kwargs["ContinuationToken"] = continuation_token
            page = self.client.list_objects_v2(**kwargs)
            for item in _object_list(page.get("Contents")):
                yield self._listed_from_item(item, None)
            if page.get("IsTruncated") is not True:
                return
            continuation_token = _optional_string(page.get("NextContinuationToken"))

    def _list_versioned(self) -> Iterator[S3ListedObject]:
        key_marker: str | None = None
        version_marker: str | None = None
        while True:
            kwargs: dict[str, object] = {"Bucket": self.bucket, "MaxKeys": 1000}
            if key_marker is not None:
                kwargs["KeyMarker"] = key_marker
            if version_marker is not None:
                kwargs["VersionIdMarker"] = version_marker
            page = self.client.list_object_versions(**kwargs)
            for item in _object_list(page.get("Versions")):
                if item.get("IsLatest") is True:
                    yield self._listed_from_item(item, _version_id(item.get("VersionId")))
            if page.get("IsTruncated") is not True:
                return
            key_marker = _optional_string(page.get("NextKeyMarker"))
            version_marker = _optional_string(page.get("NextVersionIdMarker"))

    def _listed_from_item(
        self, item: Mapping[str, object], version_id: str | None
    ) -> S3ListedObject:
        key = str(item["Key"])
        size = _required_int(item["Size"])
        last_modified = cast(datetime, item["LastModified"])
        etag = _optional_string(item.get("ETag"))
        properties = self._source_properties(key, version_id)
        return S3ListedObject(key, size, last_modified, etag, version_id, properties)


class ReadableBody(Protocol):
    """Streaming body returned by S3 object reads."""

    def read(self, amt: int | None = None) -> bytes:
        """Read body bytes."""
        ...

    def close(self) -> None:
        """Close the body."""
        ...


def _versioned_kwargs(bucket: str, key: str, version_id: str | None) -> dict[str, object]:
    kwargs: dict[str, object] = {"Bucket": bucket, "Key": key}
    if version_id is not None:
        kwargs["VersionId"] = version_id
    return kwargs


def _properties_from_head(
    head: Mapping[str, object], tags: Mapping[str, str]
) -> S3ObjectProperties:
    return S3ObjectProperties(
        size=_optional_int(head.get("ContentLength"), 0),
        etag=_optional_string(head.get("ETag")),
        content_type=_optional_string(head.get("ContentType")),
        content_encoding=_optional_string(head.get("ContentEncoding")),
        content_language=_optional_string(head.get("ContentLanguage")),
        content_disposition=_optional_string(head.get("ContentDisposition")),
        cache_control=_optional_string(head.get("CacheControl")),
        expires=cast(datetime | None, head.get("Expires")),
        metadata=_string_mapping(head.get("Metadata")),
        tags=tags,
        last_modified=cast(datetime | None, head.get("LastModified")),
        checksums=checksums_from_head_fields(head),
        checksum_type=_optional_string(head.get("ChecksumType")),
    )


def _is_not_found_error(exc: ClientError) -> bool:
    response = cast(Mapping[str, object], exc.response)
    error = response.get("Error")
    metadata = response.get("ResponseMetadata")
    code = None
    status = None
    if isinstance(error, dict):
        code = cast(Mapping[object, object], error).get("Code")
    if isinstance(metadata, dict):
        status = cast(Mapping[object, object], metadata).get("HTTPStatusCode")
    return str(code) in {"404", "NoSuchKey", "NotFound"} or status == 404


def _supports_checksum_mode(exc: ClientError) -> bool:
    response = cast(Mapping[str, object], exc.response)
    metadata = response.get("ResponseMetadata")
    status = None
    if isinstance(metadata, dict):
        status = cast(Mapping[object, object], metadata).get("HTTPStatusCode")
    return status in {400, 403}


def _object_list(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    raw_items = cast(list[object], value)
    items: list[Mapping[str, object]] = []
    for raw_item in raw_items:
        if isinstance(raw_item, dict):
            items.append(cast(Mapping[str, object], raw_item))
    return items


def _required_int(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise TypeError(f"expected integer-compatible value, got {type(value).__name__}")


def _optional_int(value: object, default: int) -> int:
    if value is None:
        return default
    return _required_int(value)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _version_id(value: object) -> str | None:
    version_id = _optional_string(value)
    if version_id == "null":
        return None
    return version_id


def _string_mapping(value: object) -> Mapping[str, str]:
    if not isinstance(value, dict):
        return {}
    raw = cast(Mapping[object, object], value)
    return {str(key): str(item) for key, item in raw.items()}
