"""Unit tests for the concrete S3 archive adapter."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.s3 import S3ObjectProperties


class FakeArchiveClient:
    """Records boto-style S3 calls."""

    list_v2_calls: list[dict[str, object]]
    version_calls: list[dict[str, object]]
    delete_calls: list[dict[str, object]]
    copy_call: dict[str, object]

    def __init__(self) -> None:
        self.list_v2_calls = []
        self.version_calls = []
        self.delete_calls = []
        self.copy_call = {}

    def get_bucket_versioning(self, *, Bucket: str) -> Mapping[str, object]:  # noqa: N803
        _ = Bucket
        return {"Status": "Suspended"}

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        self.list_v2_calls.append(kwargs)
        if "ContinuationToken" not in kwargs:
            return {
                "IsTruncated": True,
                "NextContinuationToken": "page-2",
                "Contents": [_object_item("a.txt")],
            }
        return {"IsTruncated": False, "Contents": [_object_item("b.txt")]}

    def list_object_versions(self, **kwargs: object) -> Mapping[str, object]:
        self.version_calls.append(kwargs)
        if "KeyMarker" not in kwargs:
            return {
                "IsTruncated": True,
                "NextKeyMarker": "k",
                "NextVersionIdMarker": "v",
                "DeleteMarkers": [_object_item("deleted.txt")],
                "Versions": [
                    _object_item("old.txt", version_id="old", is_latest=False),
                    _object_item("current.txt", version_id="v1", is_latest=True),
                ],
            }
        return {
            "IsTruncated": False,
            "Versions": [_object_item("null.txt", version_id="null", is_latest=True)],
        }

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        _ = kwargs
        return {
            "ContentLength": 10,
            "ETag": '"etag"',
            "ContentType": "text/plain",
            "Metadata": {"source": "yes"},
        }

    def get_object_tagging(self, **kwargs: object) -> Mapping[str, object]:
        _ = kwargs
        return {"TagSet": [{"Key": "kind", "Value": "source"}]}

    def copy_object(self, **kwargs: object) -> Mapping[str, object]:
        self.copy_call = kwargs
        return {}

    def delete_object(self, **kwargs: object) -> Mapping[str, object]:
        self.delete_calls.append(kwargs)
        return {}


def _object_item(
    key: str, *, version_id: str | None = None, is_latest: bool | None = None
) -> dict[str, object]:
    item: dict[str, object] = {
        "Key": key,
        "Size": 10,
        "LastModified": datetime(2024, 1, 1, tzinfo=UTC),
        "ETag": '"etag"',
    }
    if version_id is not None:
        item["VersionId"] = version_id
    if is_latest is not None:
        item["IsLatest"] = is_latest
    return item


@pytest.mark.unit()
def test_s3_archive_bucket_lists_unversioned_pages() -> None:
    client = FakeArchiveClient()
    bucket = S3ArchiveBucket(client, "source")

    listed = tuple(bucket.list_source_objects("Disabled"))

    assert [item.key for item in listed] == ["a.txt", "b.txt"]
    assert client.list_v2_calls[0] == {"Bucket": "source", "MaxKeys": 1000}
    assert client.list_v2_calls[1]["ContinuationToken"] == "page-2"


@pytest.mark.unit()
def test_s3_archive_bucket_lists_current_versions_and_excludes_delete_markers() -> None:
    client = FakeArchiveClient()
    bucket = S3ArchiveBucket(client, "source")

    listed = tuple(bucket.list_source_objects("Suspended"))

    assert bucket.versioning_state() == "Suspended"
    assert [(item.key, item.version_id) for item in listed] == [
        ("current.txt", "v1"),
        ("null.txt", None),
    ]
    assert client.version_calls[1]["KeyMarker"] == "k"
    assert client.version_calls[1]["VersionIdMarker"] == "v"


@pytest.mark.unit()
def test_s3_archive_bucket_copy_and_delete_use_exact_version_when_present() -> None:
    client = FakeArchiveClient()
    bucket = S3ArchiveBucket(client, "destination")

    bucket.copy_from(
        "source",
        "key.txt",
        "v1",
        S3ObjectProperties(10, '"etag"', "text/plain", None, None, None, None, None, {}, {}),
        "key.txt",
        {"fingerprint": "value"},
        "simple_native_copy",
    )
    bucket.delete_source("key.txt", "v1")
    bucket.delete_source("null.txt", None)

    assert client.copy_call["CopySource"] == {
        "Bucket": "source",
        "Key": "key.txt",
        "VersionId": "v1",
    }
    assert client.copy_call["MetadataDirective"] == "REPLACE"
    assert client.copy_call["ContentType"] == "text/plain"
    assert client.delete_calls == [
        {"Bucket": "destination", "Key": "key.txt", "VersionId": "v1"},
        {"Bucket": "destination", "Key": "null.txt"},
    ]

    with pytest.raises(NotImplementedError, match="multipart_streaming"):
        bucket.copy_from(
            "source",
            "key.txt",
            None,
            S3ObjectProperties(10, None, None, None, None, None, None, None, {}, {}),
            "key.txt",
            {},
            "multipart_streaming",
        )
