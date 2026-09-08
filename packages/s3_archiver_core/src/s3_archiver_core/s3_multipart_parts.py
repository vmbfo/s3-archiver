"""Bounded multipart concurrency for staged uploads and native copies."""

from __future__ import annotations

import base64
import hashlib
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from itertools import batched
from pathlib import Path

from s3_archiver_core.s3 import S3Client

FILE_UPLOAD_WORKERS = 4


def upload_file_parts(
    client: S3Client, bucket: str, key: str, upload_id: str, path: Path, chunk_size: int
) -> list[dict[str, object]]:
    """Upload at most four buffered parts at once and return ordered completion records."""

    def upload(number: int, chunk: bytes) -> dict[str, object]:
        response = client.upload_part(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            PartNumber=number,
            Body=chunk,
            ContentMD5=base64.b64encode(hashlib.md5(chunk).digest()).decode("ascii"),
        )
        etag = response.get("ETag")
        if etag is None:
            raise RuntimeError("S3 multipart part response omitted ETag")
        return {"PartNumber": number, "ETag": str(etag)}

    parts: list[dict[str, object]] = []
    pending: deque[Future[dict[str, object]]] = deque()
    # Executor shutdown waits for in-flight requests before the caller aborts an upload.
    with ThreadPoolExecutor(max_workers=FILE_UPLOAD_WORKERS) as pool, path.open("rb") as file:
        number = 1
        while chunk := file.read(chunk_size):
            pending.append(pool.submit(upload, number, chunk))
            number += 1
            if len(pending) >= FILE_UPLOAD_WORKERS:
                parts.append(pending.popleft().result())
        parts.extend(future.result() for future in pending)
    return parts


def copy_parts(
    copy: Callable[[tuple[int, int]], dict[str, object]], ranges: Iterable[tuple[int, int]]
) -> list[dict[str, object]]:
    """Copy at most four ranges at once; failures leave no unbounded request queue."""
    parts: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=FILE_UPLOAD_WORKERS) as pool:
        for batch in batched(ranges, FILE_UPLOAD_WORKERS):
            parts.extend(pool.map(copy, batch))
    return parts
