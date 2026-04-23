"""Typed S3 client construction and archive adapter helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast

from boto3.session import Session
from botocore.config import Config

from s3_archiver_core.settings import AppSettings, S3LocationSettings


class S3Client(Protocol):
    """Runtime-safe structural type for the S3 methods used by this package."""

    def head_bucket(self, *, Bucket: str) -> object:  # noqa: N803
        """Check bucket access."""
        ...

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        """Upload one object."""
        ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        """Return object data."""
        ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        """List unversioned objects."""
        ...

    def list_object_versions(self, **kwargs: object) -> Mapping[str, object]:
        """List object versions and delete markers."""
        ...

    def get_bucket_versioning(self, *, Bucket: str) -> Mapping[str, object]:  # noqa: N803
        """Return bucket versioning metadata."""
        ...

    def put_bucket_versioning(self, **kwargs: object) -> Mapping[str, object]:
        """Set bucket versioning metadata."""
        ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        """Return object metadata."""
        ...

    def get_object_tagging(self, **kwargs: object) -> Mapping[str, object]:
        """Return object tags."""
        ...

    def copy_object(self, **kwargs: object) -> Mapping[str, object]:
        """Copy one object server-side."""
        ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]:
        """Delete one object or object version."""
        ...

    def create_multipart_upload(self, **kwargs: object) -> Mapping[str, object]:
        """Create a multipart upload."""
        ...

    def upload_part_copy(self, **kwargs: object) -> Mapping[str, object]:
        """Copy one multipart part server-side."""
        ...

    def upload_part(self, **kwargs: object) -> Mapping[str, object]:
        """Upload one multipart part."""
        ...

    def complete_multipart_upload(self, **kwargs: object) -> Mapping[str, object]:
        """Complete a multipart upload."""
        ...

    def abort_multipart_upload(self, **kwargs: object) -> Mapping[str, object]:
        """Abort a multipart upload."""
        ...

    def put_object_tagging(self, **kwargs: object) -> Mapping[str, object]:
        """Write object tags."""
        ...


VersioningState = Literal["Disabled", "Enabled", "Suspended"]
S3_CHUNK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class S3ObjectProperties:
    """Portable object properties needed for archive verification."""

    size: int
    etag: str | None
    content_type: str | None
    content_encoding: str | None
    content_language: str | None
    content_disposition: str | None
    cache_control: str | None
    expires: datetime | None
    metadata: Mapping[str, str]
    tags: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class S3ListedObject:
    """Source object discovered by an archive listing operation."""

    key: str
    size: int
    last_modified: datetime
    etag: str | None
    version_id: str | None
    properties: S3ObjectProperties


@dataclass(frozen=True, slots=True)
class S3TransferCapabilities:
    """Transfer capabilities exposed by a source/destination pair."""

    native_copy: bool = True
    multipart_copy: bool = True
    streaming_upload: bool = True


class S3ClientFactory(Protocol):
    """Typed callable wrapper around the boto3 client factory boundary."""

    def __call__(
        self, *, service_name: Literal["s3"], endpoint_url: str, config: Config
    ) -> S3Client:
        """Build an S3 client."""
        ...


def build_s3_client(settings: AppSettings | S3LocationSettings) -> S3Client:
    """Create a configured S3 client for one S3 location."""

    location = settings.source if isinstance(settings, AppSettings) else settings
    session = Session(
        aws_access_key_id=location.access_key_id,
        aws_secret_access_key=location.secret_access_key,
        region_name=location.region,
    )
    service_name: Literal["s3"] = "s3"
    client_factory = cast(S3ClientFactory, session.client)
    return client_factory(
        service_name=service_name,
        endpoint_url=location.resolved_endpoint_url(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": location.addressing_style.value},
        ),
    )
