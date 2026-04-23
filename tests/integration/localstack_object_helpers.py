"""Host-side LocalStack object helpers for integration and e2e tests."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from botocore.response import StreamingBody
from s3_archiver_core.s3 import S3Client, build_s3_client
from s3_archiver_core.settings import AppSettings


def localstack_s3_client(
    env: Mapping[str, str], side: Literal["source", "destination"]
) -> S3Client:
    settings = AppSettings.from_env(env)
    return build_s3_client(settings.source if side == "source" else settings.destination)


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
    response = dict(
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=f"payload for {key}\n".encode() if body is None else body,
            Metadata=dict(metadata or {}),
            **kwargs,
        )
    )
    if tags:
        _ = client.put_object_tagging(
            Bucket=bucket,
            Key=key,
            Tagging={
                "TagSet": [{"Key": tag, "Value": value} for tag, value in sorted(tags.items())]
            },
        )
    return response


def listed_keys(client: S3Client, bucket: str) -> set[str]:
    response = client.list_objects_v2(Bucket=bucket)
    return {
        str(entry["Key"])
        for entry in _object_entries(response.get("Contents"))
        if entry.get("Key") is not None
    }


def listed_key_versions(client: S3Client, bucket: str, key: str) -> list[tuple[str, str, bool]]:
    versions = client.list_object_versions(Bucket=bucket, Prefix=key).get("Versions")
    return [
        (str(entry["Key"]), str(entry["VersionId"]), entry.get("IsLatest") is True)
        for entry in _object_entries(versions)
        if entry.get("Key") == key and entry.get("VersionId") is not None
    ]


def read_object_text(client: S3Client, bucket: str, key: str) -> str:
    response = client.get_object(Bucket=bucket, Key=key)
    return cast(StreamingBody, response["Body"]).read().decode()


def seed_timestamped_objects(
    client: S3Client,
    bucket: str,
    *,
    prefix: str,
    days: tuple[int, ...],
    seed_now: datetime,
) -> None:
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


def _object_entries(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [cast(dict[str, object], entry) for entry in value if isinstance(entry, dict)]
