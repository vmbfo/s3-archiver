"""Large multipart boundaries without allocating large payloads."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import override

import pytest
from s3_archiver_core import s3_transfer
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.s3 import S3_CHUNK_BYTES

from tests.unit.archive_s3_fakes import FakeArchiveClient, copy_object, properties


@pytest.mark.unit()
@pytest.mark.parametrize("gib", [80, 100])
def test_native_copy_adapts_part_ranges_through_100_gib(gib: int) -> None:
    client = FakeArchiveClient()
    size = gib * 1024**3
    copy_object(S3ArchiveBucket(client, "destination"), properties(size), "multipart_native_copy")
    calls = sorted(client.upload_part_copy_calls, key=lambda part: int(str(part["PartNumber"])))
    assert len(calls) <= 10_000
    start = 0
    for number, call in enumerate(calls, 1):
        first, last = str(call["CopySourceRange"]).removeprefix("bytes=").split("-")
        assert int(first) == start
        assert call["PartNumber"] == number
        assert call["CopySourceIfMatch"] == '"etag"'
        start = int(last) + 1
    assert start == size
    assert client.abort_calls == []


class CheckingClient(FakeArchiveClient):
    @override
    def upload_part(self, **kwargs: object) -> Mapping[str, object]:
        body = kwargs["Body"]
        assert isinstance(body, bytes)
        expected = base64.b64encode(hashlib.md5(body).digest()).decode("ascii")
        assert kwargs["ContentMD5"] == expected
        return super().upload_part(**kwargs)


@pytest.mark.unit()
def test_streamed_copy_uses_adaptive_parts_and_transport_checksums(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(s3_transfer, "S3_MAX_MULTIPART_PARTS", 3)
    monkeypatch.setattr(s3_transfer, "S3_CHUNK_BYTES", 4)
    source_client = FakeArchiveClient()
    source_client.source_body = b"abcdefghijklm"
    destination_client = CheckingClient()
    copy_object(
        S3ArchiveBucket(destination_client, "destination"),
        properties(13),
        "multipart_streaming",
        S3ArchiveBucket(source_client, "source"),
    )
    assert destination_client.upload_part_sizes == [5, 5, 3]


@pytest.mark.unit()
def test_concurrent_file_upload_checks_parts_and_completes_in_order(tmp_path: Path) -> None:
    path = tmp_path / "archive"
    _ = path.write_bytes(b"a" * (S3_CHUNK_BYTES + 1))
    client = CheckingClient()
    S3ArchiveBucket(client, "destination").upload_archive_file("key", path, {})
    assert sorted(client.upload_part_sizes) == [1, S3_CHUNK_BYTES]
    assert client.complete_calls[0]["MultipartUpload"] == {
        "Parts": [{"ETag": '"part-1"', "PartNumber": 1}, {"ETag": '"part-2"', "PartNumber": 2}]
    }


@pytest.mark.unit()
def test_adapter_conditions_source_get_without_extra_head() -> None:
    client = FakeArchiveClient()
    body = S3ArchiveBucket(client, "source").read_source_stream("key", if_match='"listed"')
    body.close()
    assert client.get_call == {"Bucket": "source", "Key": "key", "IfMatch": '"listed"'}
    assert client.head_call == {}


class FailingCopyClient(FakeArchiveClient):
    @override
    def upload_part_copy(self, **kwargs: object) -> Mapping[str, object]:
        self.upload_part_copy_calls.append(kwargs)
        raise RuntimeError("copy permission denied")


@pytest.mark.unit()
def test_native_copy_failure_does_not_queue_thousands_of_requests() -> None:
    client = FailingCopyClient()
    with pytest.raises(RuntimeError, match="permission denied"):
        copy_object(
            S3ArchiveBucket(client, "destination"),
            properties(100 * 1024**3),
            "multipart_native_copy",
        )
    assert 1 <= len(client.upload_part_copy_calls) <= 4
    assert len(client.abort_calls) == 1
    assert client.complete_calls == []
