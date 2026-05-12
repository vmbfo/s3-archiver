"""Host-side LocalStack object helpers for integration and e2e tests."""

from __future__ import annotations

import gzip
import tarfile
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Literal, cast

from botocore.exceptions import BotoCoreError, ClientError
from botocore.response import StreamingBody
from s3_archiver_core.s3 import S3Client, build_s3_client
from s3_archiver_core.settings import AppSettings

from s3_archiver_localstack_support._common import is_retryable_localstack_error, object_entries

CANONICAL_ROUTE_DATASET_DAYS = tuple(range(366))
_WRITE_RETRY_ATTEMPTS = 20
_WRITE_RETRY_DELAY_SECONDS = 1.0


def localstack_s3_client(
    env: Mapping[str, str], side: Literal["source", "destination"]
) -> S3Client:
    """Build the source or destination S3 client from LocalStack app settings."""

    settings = AppSettings.from_env(env)
    return build_s3_client(
        settings.routes[0].source if side == "source" else settings.routes[0].destination
    )


def put_test_object(
    client: S3Client,
    bucket: str,
    key: str,
    *,
    body: bytes | None = None,
    metadata: Mapping[str, str] | None = None,
    tags: Mapping[str, str] | None = None,
    **kwargs: object,
) -> dict[str, object]:
    """Put an object into LocalStack with retry handling for startup races."""

    response = dict(
        _retry_localstack_call(
            lambda: client.put_object(
                Bucket=bucket,
                Key=key,
                Body=f"payload for {key}\n".encode() if body is None else body,
                Metadata=dict(metadata or {}),
                **kwargs,
            ),
            attempts=_WRITE_RETRY_ATTEMPTS,
            delay_seconds=_WRITE_RETRY_DELAY_SECONDS,
        )
    )
    if tags:
        _ = _retry_localstack_call(
            lambda: client.put_object_tagging(
                Bucket=bucket,
                Key=key,
                Tagging={
                    "TagSet": [{"Key": tag, "Value": value} for tag, value in sorted(tags.items())]
                },
            ),
            attempts=_WRITE_RETRY_ATTEMPTS,
            delay_seconds=_WRITE_RETRY_DELAY_SECONDS,
        )
    return response


def listed_keys(client: S3Client, bucket: str) -> set[str]:
    """Return all object keys currently listed in a LocalStack bucket."""

    keys: set[str] = set()
    start_after: str | None = None
    while True:
        kwargs: dict[str, object] = {"Bucket": bucket}
        if start_after is not None:
            kwargs["StartAfter"] = start_after
        response = _retry_localstack_call(lambda kwargs=kwargs: client.list_objects_v2(**kwargs))
        last_key: str | None = None
        for entry in object_entries(response.get("Contents")):
            if entry.get("Key") is not None:
                last_key = str(entry["Key"])
                keys.add(last_key)
        if response.get("IsTruncated") is not True:
            return keys
        if last_key is None:
            raise AssertionError("LocalStack returned a truncated empty object page")
        start_after = last_key


def listed_key_versions(client: S3Client, bucket: str, key: str) -> list[tuple[str, str, bool]]:
    """Return listed versions for one key in a LocalStack bucket."""

    versions = _retry_localstack_call(
        lambda: client.list_object_versions(Bucket=bucket, Prefix=key)
    ).get("Versions")
    return [
        (str(entry["Key"]), str(entry["VersionId"]), entry.get("IsLatest") is True)
        for entry in object_entries(versions)
        if entry.get("Key") == key and entry.get("VersionId") is not None
    ]


def read_object_text(client: S3Client, bucket: str, key: str) -> str:
    """Read a LocalStack object body as text."""

    response = _retry_localstack_call(lambda: client.get_object(Bucket=bucket, Key=key))
    return cast(StreamingBody, response["Body"]).read().decode()


def read_object_bytes(client: S3Client, bucket: str, key: str) -> bytes:
    """Read a LocalStack object body as bytes."""

    response = _retry_localstack_call(lambda: client.get_object(Bucket=bucket, Key=key))
    return cast(StreamingBody, response["Body"]).read()


def read_tar_gz_members_text(client: S3Client, bucket: str, key: str) -> dict[str, str]:
    """Read text members from a tar.gz object stored in LocalStack."""

    payload = read_object_bytes(client, bucket, key)
    with (
        gzip.GzipFile(fileobj=BytesIO(payload), mode="rb") as gzip_file,
        tarfile.open(fileobj=gzip_file, mode="r:") as archive,
    ):
        members: dict[str, str] = {}
        for member in archive.getmembers():
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            members[member.name] = extracted.read().decode()
        return members


def read_tar_gz_member_pax_headers(
    client: S3Client, bucket: str, key: str
) -> dict[str, dict[str, str]]:
    """Read PAX headers from members of a tar.gz object stored in LocalStack."""

    payload = read_object_bytes(client, bucket, key)
    with (
        gzip.GzipFile(fileobj=BytesIO(payload), mode="rb") as gzip_file,
        tarfile.open(fileobj=gzip_file, mode="r:") as archive,
    ):
        return {member.name: dict(member.pax_headers) for member in archive.getmembers()}


def seed_timestamped_objects(
    client: S3Client,
    bucket: str,
    *,
    prefix: str,
    days: tuple[int, ...],
    seed_now: datetime,
) -> None:
    """Seed timestamped LocalStack objects with deterministic age metadata."""

    seeded_now = seed_now.astimezone(UTC).replace(microsecond=0)
    for day in days:
        target = seeded_now - timedelta(days=day)
        _ = put_test_object(
            client,
            bucket,
            f"{prefix}/age-{day}-days.txt",
            metadata={
                "s3-archiver-test-age-days": str(day),
                "s3-archiver-test-last-modified": target.isoformat(),
            },
        )


def seed_canonical_route_dataset(
    client: S3Client,
    bucket: str,
    *,
    prefix: str,
    seed_now: datetime,
) -> None:
    """Seed the canonical 366-day LocalStack route fixture."""

    seed_timestamped_objects(
        client,
        bucket,
        prefix=prefix,
        days=CANONICAL_ROUTE_DATASET_DAYS,
        seed_now=seed_now,
    )


def route_dataset_keys(
    prefix: str, *, days: tuple[int, ...] = CANONICAL_ROUTE_DATASET_DAYS
) -> set[str]:
    """Return the expected keys for the canonical route fixture."""

    return {f"{prefix}/age-{day}-days.txt" for day in days}


def archive_eligible_days(
    minimum_age_days: int,
    *,
    days: tuple[int, ...] = CANONICAL_ROUTE_DATASET_DAYS,
) -> set[int]:
    """Return fixture ages older than the archive minimum age."""

    return {day for day in days if day > minimum_age_days}


def _retry_localstack_call(
    operation: Callable[[], Mapping[str, object]],
    *,
    attempts: int = 5,
    delay_seconds: float = 0.5,
) -> Mapping[str, object]:
    for attempt in range(attempts):
        try:
            return operation()
        except (BotoCoreError, ClientError) as exc:
            if attempt == attempts - 1 or not is_retryable_localstack_error(exc):
                raise
            time.sleep(delay_seconds)
    raise AssertionError("LocalStack retry loop exhausted without returning")  # pragma: no cover
