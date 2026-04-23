from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import override

import pytest
from botocore.exceptions import ClientError
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.s3 import S3_CHUNK_BYTES

from tests.unit.archive_s3_fakes import (
    FakeArchiveClient,
    client_error,
    copy_object,
    properties,
)


class MissingObjectClient(FakeArchiveClient):
    @override
    def get_object(self, **kwargs: object) -> dict[str, object]:
        _ = kwargs
        raise client_error("NoSuchKey")


class DeniedObjectClient(FakeArchiveClient):
    @override
    def get_object(self, **kwargs: object) -> dict[str, object]:
        _ = kwargs
        raise client_error("AccessDenied", 403)


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
        bucket,
        "source",
        "key.txt",
        "v1",
        properties(),
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


@pytest.mark.unit()
def test_s3_archive_bucket_multipart_native_copy_preserves_properties() -> None:
    client = FakeArchiveClient()
    bucket = S3ArchiveBucket(client, "destination")

    copy_object(bucket, properties(S3_CHUNK_BYTES + 1), "multipart_native_copy")

    assert client.create_calls[0]["Metadata"] == {"source": "yes", "fingerprint": "value"}
    assert client.create_calls[0]["ContentEncoding"] == "gzip"
    assert [call["CopySourceRange"] for call in client.upload_part_copy_calls] == [
        f"bytes=0-{S3_CHUNK_BYTES - 1}",
        f"bytes={S3_CHUNK_BYTES}-{S3_CHUNK_BYTES}",
    ]
    assert client.complete_calls[0]["MultipartUpload"] == {
        "Parts": [{"ETag": '"copy-1"', "PartNumber": 1}, {"ETag": '"copy-2"', "PartNumber": 2}]
    }
    assert client.tagging_calls[0]["Tagging"] == {"TagSet": [{"Key": "kind", "Value": "source"}]}


@pytest.mark.unit()
def test_s3_archive_bucket_streaming_upload_uses_bounded_parts() -> None:
    source_client = FakeArchiveClient()
    destination_client = FakeArchiveClient()
    source_client.source_body = b"a" * (S3_CHUNK_BYTES + 1)
    source = S3ArchiveBucket(source_client, "source")
    bucket = S3ArchiveBucket(destination_client, "destination")

    copy_object(bucket, properties(len(source_client.source_body)), "multipart_streaming", source)

    assert source_client.get_call == {"Bucket": "source", "Key": "large.bin", "VersionId": "v1"}
    assert destination_client.get_call == {}
    assert destination_client.upload_part_sizes == [S3_CHUNK_BYTES, 1]
    assert destination_client.abort_calls == []
    assert destination_client.tagging_calls[0]["Bucket"] == "destination"


@pytest.mark.unit()
def test_s3_archive_bucket_content_sha256_hashes_body_and_handles_missing() -> None:
    client = FakeArchiveClient()
    client.source_body = b"abc"

    assert S3ArchiveBucket(client, "source").content_sha256("key") == sha256(b"abc").hexdigest()
    assert S3ArchiveBucket(MissingObjectClient(), "source").content_sha256("missing") is None
    with pytest.raises(ClientError):
        _ = S3ArchiveBucket(DeniedObjectClient(), "source").content_sha256("denied")


@pytest.mark.unit()
def test_s3_archive_bucket_temp_file_transfer_cleans_up_on_failure(
    tmp_path: Path,
) -> None:
    source_client = FakeArchiveClient()
    destination_client = FakeArchiveClient()
    source_client.source_body = b"a" * 4
    destination_client.fail_upload_part = True
    source = S3ArchiveBucket(source_client, "source")
    bucket = S3ArchiveBucket(destination_client, "destination", tmp_path)

    with pytest.raises(RuntimeError, match="upload failed"):
        copy_object(bucket, properties(len(source_client.source_body)), "temp_file_backed", source)

    assert source_client.get_call == {"Bucket": "source", "Key": "large.bin", "VersionId": "v1"}
    assert destination_client.get_call == {}
    assert destination_client.abort_calls == [
        {"Bucket": "destination", "Key": "large.bin", "UploadId": "upload-1"}
    ]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.unit()
def test_s3_archive_bucket_temp_file_transfer_uses_dedicated_dir(tmp_path: Path) -> None:
    source_client = FakeArchiveClient()
    destination_client = FakeArchiveClient()
    source_client.source_body = b"a" * 4
    unrelated_dir = tmp_path / "unrelated"
    temp_dir = tmp_path / "runtime-temp"
    unrelated_dir.mkdir()
    source = S3ArchiveBucket(source_client, "source", unrelated_dir)
    bucket = S3ArchiveBucket(destination_client, "destination", temp_dir)

    copy_object(bucket, properties(len(source_client.source_body)), "temp_file_backed", source)

    assert list(temp_dir.iterdir()) == []
    assert list(unrelated_dir.iterdir()) == []


@pytest.mark.unit()
def test_s3_archive_bucket_head_and_tag_failures_are_not_synthesized() -> None:
    client = FakeArchiveClient()
    bucket = S3ArchiveBucket(client, "source")

    client.head_error = client_error("404")
    assert bucket.head_object("missing.txt") is None
    with pytest.raises(FileNotFoundError, match="listed source object disappeared"):
        _ = tuple(bucket.list_source_objects("Disabled"))

    client.head_error = client_error("AccessDenied", 403)
    with pytest.raises(ClientError):
        _ = bucket.head_object("denied.txt")

    client.head_error = None
    client.tag_error = RuntimeError("tag read failed")
    with pytest.raises(RuntimeError, match="tag read failed"):
        _ = bucket.head_object("key.txt")
