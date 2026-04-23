"""Concrete S3 archive bucket adapter."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, cast

from s3_archiver_core.archive_transfer import TransferStrategy
from s3_archiver_core.s3 import (
    S3ListedObject,
    S3ObjectProperties,
    VersioningState,
)


class ArchiveS3Client(Protocol):
    """Runtime S3 protocol used by the archive adapter."""

    def get_bucket_versioning(self, *, Bucket: str) -> Mapping[str, object]:  # noqa: N803
        """Read bucket versioning state."""
        ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        """List current bucket objects."""
        ...

    def list_object_versions(self, **kwargs: object) -> Mapping[str, object]:
        """List bucket versions."""
        ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        """Read object headers."""
        ...

    def get_object_tagging(self, **kwargs: object) -> Mapping[str, object]:
        """Read object tags."""
        ...

    def copy_object(self, **kwargs: object) -> Mapping[str, object]:
        """Copy an object."""
        ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]:
        """Delete an object."""
        ...


@dataclass(frozen=True, slots=True)
class S3ArchiveBucket:
    """Bucket-scoped S3 operations used by the archive engine."""

    client: ArchiveS3Client
    bucket: str

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
            head = self.client.head_object(**kwargs)
            tags = self.get_tags(key, version_id)
        except Exception:
            return None
        return _properties_from_head(head, tags)

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
        source_bucket: str,
        source_key: str,
        source_version_id: str | None,
        properties: S3ObjectProperties,
        destination_key: str,
        destination_metadata: Mapping[str, str],
        strategy: TransferStrategy,
    ) -> None:
        """Copy one source object into this bucket while preserving portable properties."""

        if strategy != "simple_native_copy":
            raise NotImplementedError(f"{strategy} transfer is not implemented by S3ArchiveBucket")
        copy_source = {"Bucket": source_bucket, "Key": source_key}
        if source_version_id is not None:
            copy_source["VersionId"] = source_version_id
        kwargs = _copy_kwargs(
            self.bucket, destination_key, copy_source, properties, destination_metadata
        )
        _ = self.client.copy_object(**kwargs)

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
        properties = self.head_object(key, version_id)
        if properties is None:
            properties = S3ObjectProperties(size, etag, None, None, None, None, None, None, {}, {})
        return S3ListedObject(key, size, last_modified, etag, version_id, properties)


def _versioned_kwargs(bucket: str, key: str, version_id: str | None) -> dict[str, object]:
    kwargs: dict[str, object] = {"Bucket": bucket, "Key": key}
    if version_id is not None:
        kwargs["VersionId"] = version_id
    return kwargs


def _copy_kwargs(
    bucket: str,
    key: str,
    copy_source: Mapping[str, str],
    properties: S3ObjectProperties,
    metadata: Mapping[str, str],
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "Bucket": bucket,
        "Key": key,
        "CopySource": copy_source,
        "Metadata": dict(metadata),
        "MetadataDirective": "REPLACE",
        "TaggingDirective": "COPY",
    }
    _add_optional(kwargs, "ContentType", properties.content_type)
    _add_optional(kwargs, "ContentEncoding", properties.content_encoding)
    _add_optional(kwargs, "ContentLanguage", properties.content_language)
    _add_optional(kwargs, "ContentDisposition", properties.content_disposition)
    _add_optional(kwargs, "CacheControl", properties.cache_control)
    _add_optional(kwargs, "Expires", properties.expires)
    return kwargs


def _add_optional(target: dict[str, object], key: str, value: object | None) -> None:
    if value is not None:
        target[key] = value


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
    )


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
