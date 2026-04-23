"""Unit tests for S3 archive adapter and transfer edge cases."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.s3 import S3ObjectProperties

from tests.unit.archive_s3_fakes import FakeArchiveClient, copy_object, properties


class VersioningClient(FakeArchiveClient):
    """Fake client with configurable versioning status."""

    def __init__(self, status: str | None) -> None:
        super().__init__()
        self.status: str | None = status

    @override
    def get_bucket_versioning(self, *, Bucket: str) -> Mapping[str, object]:
        response: dict[str, object] = {"Bucket": Bucket}
        if self.status is not None:
            response["Status"] = self.status
        return response


class StaticPageClient(FakeArchiveClient):
    """Fake client returning one configured list page."""

    def __init__(self, page: Mapping[str, object]) -> None:
        super().__init__()
        self.page: Mapping[str, object] = page

    @override
    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        self.list_v2_calls.append(kwargs)
        return self.page


class TagShapeClient(FakeArchiveClient):
    """Fake client returning a configured tag response."""

    def __init__(self, response: Mapping[str, object]) -> None:
        super().__init__()
        self.response: Mapping[str, object] = response

    @override
    def get_object_tagging(self, **kwargs: object) -> Mapping[str, object]:
        _ = kwargs
        return self.response


class HeadShapeClient(FakeArchiveClient):
    """Fake client returning a configured head response."""

    def __init__(self, response: Mapping[str, object]) -> None:
        super().__init__()
        self.response: Mapping[str, object] = response

    @override
    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        self.head_call = dict(kwargs)
        return self.response


class FailingMultipartCopyClient(FakeArchiveClient):
    @override
    def upload_part_copy(self, **kwargs: object) -> Mapping[str, object]:
        _ = kwargs
        raise RuntimeError("copy part failed")


class MissingUploadIdClient(FakeArchiveClient):
    @override
    def create_multipart_upload(self, **kwargs: object) -> Mapping[str, object]:
        self.create_calls.append(kwargs)
        return {}


class MissingPartEtagClient(FakeArchiveClient):
    @override
    def upload_part(self, **kwargs: object) -> Mapping[str, object]:
        body = kwargs["Body"]
        assert isinstance(body, bytes)
        self.upload_part_sizes.append(len(body))
        return {}


class NonReadableBodyClient(FakeArchiveClient):
    @override
    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        return self._set("get_call", kwargs, {"Body": object()})


class RaisingBody:
    def read(self, size: int = -1) -> bytes:
        _ = size
        raise RuntimeError("read failed")


class RaisingBodyClient(FakeArchiveClient):
    @override
    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        return self._set("get_call", kwargs, {"Body": RaisingBody()})


@pytest.mark.unit()
def test_s3_archive_bucket_versioning_state_defaults_to_disabled() -> None:
    assert S3ArchiveBucket(VersioningClient("Enabled"), "source").versioning_state() == "Enabled"
    assert S3ArchiveBucket(VersioningClient(None), "source").versioning_state() == "Disabled"


@pytest.mark.unit()
def test_s3_archive_bucket_tag_parsing_ignores_invalid_shapes() -> None:
    assert S3ArchiveBucket(TagShapeClient({"TagSet": "bad"}), "source").get_tags("key") == {}
    assert S3ArchiveBucket(
        TagShapeClient(
            {
                "TagSet": [
                    object(),
                    {"Key": "kept", "Value": 3},
                    {"Key": "missing-value"},
                ]
            }
        ),
        "source",
    ).get_tags("key") == {"kept": "3"}


@pytest.mark.unit()
def test_s3_archive_bucket_rejects_non_s3_archive_source() -> None:
    bucket = S3ArchiveBucket(FakeArchiveClient(), "destination")

    with pytest.raises(TypeError, match="requires an S3ArchiveBucket source"):
        bucket.copy_from(
            object(),
            "source",
            "key",
            None,
            properties(),
            "key",
            {},
            "simple_native_copy",
        )


@pytest.mark.unit()
def test_s3_archive_bucket_list_and_head_coerce_s3_shapes() -> None:
    listed = tuple(
        S3ArchiveBucket(
            StaticPageClient(
                {
                    "IsTruncated": False,
                    "Contents": [
                        object(),
                        {
                            "Key": "from-string.txt",
                            "Size": "10",
                            "LastModified": datetime(2024, 1, 1, tzinfo=UTC),
                        },
                    ],
                }
            ),
            "source",
        ).list_source_objects("Disabled")
    )
    assert [(item.key, item.size, item.etag) for item in listed] == [("from-string.txt", 10, None)]

    assert (
        tuple(
            S3ArchiveBucket(
                StaticPageClient({"IsTruncated": False, "Contents": "bad"}),
                "source",
            ).list_source_objects("Disabled")
        )
        == ()
    )

    with pytest.raises(TypeError, match="expected integer-compatible"):
        _ = tuple(
            S3ArchiveBucket(
                StaticPageClient(
                    {
                        "IsTruncated": False,
                        "Contents": [
                            {
                                "Key": "bad-size.txt",
                                "Size": object(),
                                "LastModified": datetime(2024, 1, 1, tzinfo=UTC),
                            }
                        ],
                    }
                ),
                "source",
            ).list_source_objects("Disabled")
        )


@pytest.mark.unit()
def test_s3_archive_bucket_head_defaults_and_coerces_metadata() -> None:
    bucket = S3ArchiveBucket(
        HeadShapeClient({"ETag": 3, "Metadata": "bad"}),
        "source",
    )

    assert bucket.head_object("key") == S3ObjectProperties(
        0, "3", None, None, None, None, None, None, {}, {"kind": "source"}, None, {}
    )


@pytest.mark.unit()
def test_s3_archive_bucket_head_collects_last_modified_and_checksums() -> None:
    client = HeadShapeClient(
        {
            "LastModified": datetime(2024, 1, 1, tzinfo=UTC),
            "ChecksumSHA256": "sha256-value",
            "ChecksumCRC32": "crc32-value",
        }
    )
    bucket = S3ArchiveBucket(client, "source")

    properties = bucket.head_object("key")

    assert properties is not None
    assert client.head_call["ChecksumMode"] == "ENABLED"
    assert properties.last_modified == datetime(2024, 1, 1, tzinfo=UTC)
    assert properties.checksums == {"sha256": "sha256-value", "crc32": "crc32-value"}


@pytest.mark.unit()
def test_s3_transfer_zero_size_streaming_uses_put_object() -> None:
    source_client = FakeArchiveClient()
    destination_client = FakeArchiveClient()
    source = S3ArchiveBucket(source_client, "source")
    destination = S3ArchiveBucket(destination_client, "destination")

    copy_object(destination, properties(0), "multipart_streaming", source)

    assert destination_client.put_call["Body"] == b""
    assert destination_client.create_calls == []
    assert destination_client.tagging_calls[0]["Bucket"] == "destination"


@pytest.mark.unit()
def test_s3_transfer_multipart_copy_aborts_on_part_failure() -> None:
    client = FailingMultipartCopyClient()
    bucket = S3ArchiveBucket(client, "destination")

    with pytest.raises(RuntimeError, match="copy part failed"):
        copy_object(bucket, properties(10), "multipart_native_copy")

    assert client.abort_calls == [
        {"Bucket": "destination", "Key": "large.bin", "UploadId": "upload-1"}
    ]


@pytest.mark.unit()
def test_s3_transfer_rejects_missing_upload_id_and_part_etag() -> None:
    source_client = FakeArchiveClient()
    source_client.source_body = b"body"
    missing_upload_id = S3ArchiveBucket(MissingUploadIdClient(), "destination")

    with pytest.raises(RuntimeError, match="omitted UploadId"):
        copy_object(
            missing_upload_id,
            properties(len(source_client.source_body)),
            "multipart_streaming",
            S3ArchiveBucket(source_client, "source"),
        )

    missing_etag_client = MissingPartEtagClient()
    source_client.source_body = b"body"
    with pytest.raises(RuntimeError, match="omitted ETag"):
        copy_object(
            S3ArchiveBucket(missing_etag_client, "destination"),
            properties(len(source_client.source_body)),
            "multipart_streaming",
            S3ArchiveBucket(source_client, "source"),
        )
    assert missing_etag_client.abort_calls == [
        {"Bucket": "destination", "Key": "large.bin", "UploadId": "upload-1"}
    ]


@pytest.mark.unit()
def test_s3_transfer_rejects_non_readable_body() -> None:
    with pytest.raises(TypeError, match="Body is not readable"):
        copy_object(
            S3ArchiveBucket(FakeArchiveClient(), "destination"),
            properties(1),
            "multipart_streaming",
            S3ArchiveBucket(NonReadableBodyClient(), "source"),
        )


@pytest.mark.unit()
def test_s3_transfer_temp_file_stage_failure_cleans_partial_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="read failed"):
        copy_object(
            S3ArchiveBucket(FakeArchiveClient(), "destination", tmp_path),
            properties(1),
            "temp_file_backed",
            S3ArchiveBucket(RaisingBodyClient(), "source"),
        )

    assert list(tmp_path.iterdir()) == []
