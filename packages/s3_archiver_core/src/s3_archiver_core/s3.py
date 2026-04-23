"""Typed S3 client construction and archive adapter helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol, cast

from boto3.session import Session
from botocore.config import Config

from s3_archiver_core.settings import AppSettings

if TYPE_CHECKING:
    from mypy_boto3_s3.client import S3Client
else:

    class S3Client(Protocol):
        """Runtime-safe structural type for the subset of S3 methods we use."""

        def head_bucket(self, *, Bucket: str) -> object:  # noqa: N803
            """Check whether a bucket is reachable."""
            ...

        def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> object:  # noqa: N803
            """Write an object to S3."""
            ...

        def get_object(self, **kwargs: object) -> Mapping[str, object]:
            """Fetch an object from S3."""
            ...

        def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
            """List objects in a bucket."""
            ...

        def list_object_versions(self, **kwargs: object) -> Mapping[str, object]:
            """List object versions in a bucket."""
            ...

        def get_bucket_versioning(self, *, Bucket: str) -> Mapping[str, object]:  # noqa: N803
            """Read bucket versioning state."""
            ...

        def head_object(self, **kwargs: object) -> Mapping[str, object]:
            """Read object headers and metadata."""
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


VersioningState = Literal["Disabled", "Enabled", "Suspended"]


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


def build_s3_client(settings: AppSettings) -> S3Client:
    """Create a configured S3 client for the current runtime settings."""

    session = Session(
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        region_name=settings.region,
    )
    service_name: Literal["s3"] = "s3"
    client_factory = cast(S3ClientFactory, session.client)
    return client_factory(
        service_name=service_name,
        endpoint_url=settings.resolved_endpoint_url(),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.addressing_style.value},
        ),
    )
