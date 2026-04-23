"""Private S3 archive adapter protocols."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol


class ArchiveS3Client(Protocol):
    """Runtime S3 protocol used by the archive adapter."""

    def get_bucket_versioning(self, *, Bucket: str) -> Mapping[str, object]:  # noqa: N803
        """Return bucket versioning metadata."""
        ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        """List unversioned objects."""
        ...

    def list_object_versions(self, **kwargs: object) -> Mapping[str, object]:
        """List object versions and delete markers."""
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

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        """Return object data."""
        ...

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        """Upload one object."""
        ...

    def put_object_tagging(self, **kwargs: object) -> Mapping[str, object]:
        """Write object tags."""
        ...

    def delete_object(self, **kwargs: object) -> Mapping[str, object]:
        """Delete one object or object version."""
        ...
