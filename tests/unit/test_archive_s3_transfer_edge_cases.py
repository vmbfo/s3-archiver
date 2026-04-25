"""Unit tests for S3 transfer edge cases."""

from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
from pathlib import Path
from typing import override

import pytest
from s3_archiver_core.archive_s3 import S3ArchiveBucket

from tests.unit.archive_s3_fakes import FakeArchiveClient, copy_object, properties


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


class CloseTrackingBody(BytesIO):
    closed_count: int

    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.closed_count = 0

    @override
    def close(self) -> None:
        self.closed_count += 1
        super().close()


class RaisingBodyClient(FakeArchiveClient):
    @override
    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        return self._set("get_call", kwargs, {"Body": RaisingBody()})


class CloseTrackingBodyClient(FakeArchiveClient):
    body: CloseTrackingBody

    def __init__(self) -> None:
        super().__init__()
        self.body = CloseTrackingBody(b"body")

    @override
    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        return self._set("get_call", kwargs, {"Body": self.body})


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
def test_s3_transfer_closes_streaming_source_body() -> None:
    source_client = CloseTrackingBodyClient()

    copy_object(
        S3ArchiveBucket(FakeArchiveClient(), "destination"),
        properties(4),
        "multipart_streaming",
        S3ArchiveBucket(source_client, "source"),
    )

    assert source_client.body.closed is True
    assert source_client.body.closed_count == 1


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
