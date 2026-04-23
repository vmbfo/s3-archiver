"""LocalStack-only test harness helpers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

LOCALSTACK_HOST_ENDPOINT = "http://127.0.0.1:4566"
LOCALSTACK_COMPOSE_ENDPOINT = "http://localstack:4566"
LOCALSTACK_ENDPOINT_HOSTS = frozenset(
    {"127.0.0.1", "localhost", "localstack", "localhost.localstack.cloud"}
)


@dataclass(frozen=True, slots=True)
class LocalstackBucketPair:
    source: str
    destination: str


def new_localstack_bucket_pair() -> LocalstackBucketPair:
    suffix = uuid.uuid4().hex
    return LocalstackBucketPair(
        source=f"s3-archiver-source-{suffix}",
        destination=f"s3-archiver-destination-{suffix}",
    )


def bucket_pair_from_env(env: dict[str, str]) -> LocalstackBucketPair:
    return LocalstackBucketPair(
        source=env["TEST_S3_SOURCE_BUCKET"],
        destination=env["TEST_S3_DESTINATION_BUCKET"],
    )


def localstack_test_env(
    bucket_pair: LocalstackBucketPair,
    *,
    endpoint: str,
    log_dir: str,
) -> dict[str, str]:
    env = {
        "S3_PROVIDER": "localstack",
        "S3_ACCESS_KEY_ID": "test",
        "S3_SECRET_ACCESS_KEY": "test",
        "S3_REGION": "us-east-1",
        "S3_BUCKET": bucket_pair.source,
        "S3_ENDPOINT_URL": endpoint,
        "S3_ADDRESSING_STYLE": "path",
        "S3_SOURCE_PROVIDER": "localstack",
        "S3_SOURCE_ACCESS_KEY_ID": "test",
        "S3_SOURCE_SECRET_ACCESS_KEY": "test",
        "S3_SOURCE_REGION": "us-east-1",
        "S3_SOURCE_BUCKET": bucket_pair.source,
        "S3_SOURCE_ENDPOINT_URL": endpoint,
        "S3_SOURCE_ADDRESSING_STYLE": "path",
        "S3_DESTINATION_PROVIDER": "localstack",
        "S3_DESTINATION_ACCESS_KEY_ID": "test",
        "S3_DESTINATION_SECRET_ACCESS_KEY": "test",
        "S3_DESTINATION_REGION": "us-east-1",
        "S3_DESTINATION_BUCKET": bucket_pair.destination,
        "S3_DESTINATION_ENDPOINT_URL": endpoint,
        "S3_DESTINATION_ADDRESSING_STYLE": "path",
        "LOG_LEVEL": "INFO",
        "LOG_DIR": log_dir,
    }
    assert_localstack_test_target(env)
    return env


def write_localstack_env_file(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
    *,
    endpoint: str,
    log_dir: str,
) -> Path:
    env = localstack_test_env(bucket_pair, endpoint=endpoint, log_dir=log_dir)
    env_file = tmp_path / "localstack.env"
    _ = env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(env.items())),
        encoding="utf-8",
    )
    return env_file


def assert_localstack_test_target(env: dict[str, str]) -> None:
    for field in ("S3_SOURCE_PROVIDER", "S3_DESTINATION_PROVIDER"):
        if env.get(field) != "localstack":
            raise RuntimeError(f"{field} must be 'localstack' for integration/e2e tests")
    for field in ("S3_SOURCE_ENDPOINT_URL", "S3_DESTINATION_ENDPOINT_URL"):
        _assert_localstack_endpoint(field, env.get(field))


def _assert_localstack_endpoint(field: str, endpoint: str | None) -> None:
    if endpoint is None:
        raise RuntimeError(f"{field} must be set for integration/e2e tests")
    host = urlparse(endpoint).hostname
    if host not in LOCALSTACK_ENDPOINT_HOSTS:
        raise RuntimeError(f"{field} host {host!r} is not allowed for integration/e2e tests")
