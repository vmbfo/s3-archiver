"""LocalStack bucket-pair lifecycle helpers."""

from __future__ import annotations

from typing import cast

from s3_archiver_core.s3 import build_s3_client
from s3_archiver_core.settings import AppSettings

from s3_archiver_localstack_support.harness import (
    LocalstackBucketPair,
    LocalstackS3AdminClient,
    delete_localstack_bucket,
    ensure_localstack_bucket,
)


def localstack_admin_client(settings: AppSettings) -> LocalstackS3AdminClient:
    """Build an S3 admin client from LocalStack app settings."""

    return cast(LocalstackS3AdminClient, _as_object(build_s3_client(settings.routes[0].source)))


def ensure_localstack_bucket_pair(
    client: LocalstackS3AdminClient, bucket_pair: LocalstackBucketPair
) -> None:
    """Create both buckets for an isolated LocalStack run."""

    ensure_localstack_bucket(client, bucket_pair.source)
    ensure_localstack_bucket(client, bucket_pair.destination)


def delete_localstack_bucket_pair(
    client: LocalstackS3AdminClient,
    bucket_pair: LocalstackBucketPair,
    *,
    context: str = "LocalStack buckets",
) -> None:
    """Delete both buckets for an isolated LocalStack run."""

    failures: list[str] = []
    for bucket in (bucket_pair.source, bucket_pair.destination):
        try:
            delete_localstack_bucket(client, bucket)
        except RuntimeError as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError(f"Failed to tear down {context}: " + "; ".join(failures))


def _as_object(value: object) -> object:
    return value
