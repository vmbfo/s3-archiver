"""Fakes used by LocalStack support unit tests."""

from __future__ import annotations

import gzip
import tarfile
from collections.abc import Mapping
from io import BytesIO
from typing import cast

from botocore.response import StreamingBody
from s3_archiver_core.s3 import S3Client


class FakeAdminClient:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted_buckets: list[str] = []
        self.deleted_objects: list[dict[str, object]] = []
        self.version_pages: list[dict[str, object]] = []
        self.object_pages: list[dict[str, object]] = []
        self.delete_bucket_errors: list[Exception] = []

    def head_bucket(self, *, Bucket: str) -> object:  # noqa: N803
        return {"Bucket": Bucket}

    def create_bucket(self, *, Bucket: str) -> object:  # noqa: N803
        self.created.append(Bucket)
        return {"Bucket": Bucket}

    def list_buckets(self) -> object:
        return {"Buckets": []}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        _ = kwargs
        return self.object_pages.pop(0) if self.object_pages else {"IsTruncated": False}

    def list_object_versions(self, **kwargs: object) -> dict[str, object]:
        _ = kwargs
        return self.version_pages.pop(0) if self.version_pages else {"IsTruncated": False}

    def delete_objects(self, *, Bucket: str, Delete: dict[str, object]) -> object:  # noqa: N803
        self.deleted_objects.append({"Bucket": Bucket, "Delete": Delete})
        return {}

    def delete_bucket(self, *, Bucket: str) -> object:  # noqa: N803
        if self.delete_bucket_errors:
            raise self.delete_bucket_errors.pop(0)
        self.deleted_buckets.append(Bucket)
        return {}


class FakeObjectClient:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []
        self.tagging_calls: list[dict[str, object]] = []
        self.object_pages: list[dict[str, object]] = []
        self.version_payload: dict[str, object] = {}
        self.objects: dict[str, bytes] = {}
        self.put_errors: list[Exception] = []

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        if self.put_errors:
            raise self.put_errors.pop(0)
        self.put_calls.append(dict(kwargs))
        return {"ETag": "etag"}

    def put_object_tagging(self, **kwargs: object) -> Mapping[str, object]:
        self.tagging_calls.append(dict(kwargs))
        return {}

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        _ = kwargs
        return self.object_pages.pop(0) if self.object_pages else {"IsTruncated": False}

    def list_object_versions(self, **kwargs: object) -> Mapping[str, object]:
        _ = kwargs
        return self.version_payload

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, object]:  # noqa: N803
        _ = Bucket
        body = self.objects[Key]
        return {"Body": StreamingBody(BytesIO(body), len(body))}


def tar_gz_payload(members: dict[str, bytes], *, directories: tuple[str, ...] = ()) -> bytes:
    payload = BytesIO()
    with (
        gzip.GzipFile(fileobj=payload, mode="wb") as gzip_file,
        tarfile.open(fileobj=gzip_file, mode="w:") as archive,
    ):
        for name in directories:
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            archive.addfile(info)
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, BytesIO(content))
    return payload.getvalue()


def as_s3_client(client: FakeObjectClient) -> S3Client:
    return cast(S3Client, cast(object, client))
