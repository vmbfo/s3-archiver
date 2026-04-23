"""LocalStack timestamp seed helper integration tests."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import cast

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from s3_archiver_core.s3 import S3Client, build_s3_client
from s3_archiver_core.settings import AppSettings

from tests.integration.localstack_harness import (
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    localstack_test_env,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_NOW = datetime(2100, 1, 1, tzinfo=UTC)


@pytest.mark.integration()
def test_timestamp_seed_helper_sets_exact_last_modified_values(
    compose_env: dict[str, str],
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    result = run_timestamp_seed_helper(
        compose_env,
        prefix="seed-helper",
        days=(0, 60, 61),
        seed_now=SEED_NOW,
    )
    settings = AppSettings.from_env(_integration_env(localstack_bucket_pair))
    client = build_s3_client(settings)

    rows = [line.split("\t") for line in result.stdout.splitlines()]

    assert [row[1] for row in rows] == ["0", "60", "61"]
    for key, age_days, last_modified in rows:
        expected = SEED_NOW - timedelta(days=int(age_days))
        assert key == f"seed-helper/age-{age_days}-days.txt"
        assert _parse_timestamp(last_modified) == expected
        head = _head_object_with_retry(client, settings.bucket, key)
        metadata = cast(dict[str, object], head.get("Metadata", {}))
        assert metadata.get("s3-archiver-test-age-days") == age_days
        assert _required_datetime(head, "LastModified") == expected


def run_timestamp_seed_helper(
    compose_env: Mapping[str, str],
    *,
    prefix: str,
    days: tuple[int, ...],
    seed_now: datetime,
) -> subprocess.CompletedProcess[str]:
    days_value = " ".join(str(day) for day in days)
    source_bucket = compose_env["TEST_S3_SOURCE_BUCKET"]
    command = (
        f"TEST_S3_SOURCE_BUCKET={source_bucket} "
        f"TEST_TIMESTAMP_SEED_PREFIX={prefix} "
        f"TEST_TIMESTAMP_SEED_DAYS='{days_value}' "
        f"TEST_TIMESTAMP_SEED_NOW={seed_now.isoformat()} "
        "/opt/s3-archiver-test-support/seed-object-timestamps.sh"
    )
    exec_command = [
        "docker",
        "compose",
        "--profile",
        "test",
        "exec",
        "-T",
        "localstack",
        "sh",
        "-lc",
        command,
    ]
    up_command = ["docker", "compose", "--profile", "test", "up", "-d", "localstack"]
    for attempt in range(5):
        try:
            return subprocess.run(
                exec_command,
                cwd=REPO_ROOT,
                env=dict(compose_env),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raw_stderr = cast(object, exc.stderr)
            stderr = raw_stderr if isinstance(raw_stderr, str) else ""
            if attempt == 4 or not _is_retryable_seed_helper_error(exc.returncode, stderr):
                raise
            if _localstack_needs_restart(stderr):
                _ = subprocess.run(
                    up_command,
                    cwd=REPO_ROOT,
                    env=dict(compose_env),
                    check=True,
                    capture_output=True,
                    text=True,
                )
            time.sleep(0.5)
    raise AssertionError("timestamp seed helper retry loop exhausted without returning")


def _integration_env(bucket_pair: LocalstackBucketPair) -> dict[str, str]:
    endpoint = os.environ.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT)
    return localstack_test_env(
        bucket_pair,
        endpoint=endpoint,
        log_dir=str(REPO_ROOT / ".local" / "integration-runtime" / "var" / "log"),
    )


def _required_datetime(head: Mapping[str, object], field: str) -> datetime:
    value = head.get(field)
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    return value


def _parse_timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return parsedate_to_datetime(value)


def _is_retryable_seed_helper_error(returncode: int, stderr: str) -> bool:
    return returncode == 137 or _localstack_needs_restart(stderr)


def _localstack_needs_restart(stderr: str) -> bool:
    return 'service "localstack" is not running' in stderr or (
        "container " in stderr and " is not running" in stderr
    )


def _head_object_with_retry(
    client: S3Client,
    bucket: str,
    key: str,
    *,
    attempts: int = 5,
    delay_seconds: float = 0.5,
) -> Mapping[str, object]:
    for attempt in range(attempts):
        try:
            return client.head_object(Bucket=bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            if attempt == attempts - 1 or not _is_retryable_head_error(exc):
                raise
            time.sleep(delay_seconds)
    raise AssertionError("head_object retry loop exhausted without returning")


def _is_retryable_head_error(exc: Exception) -> bool:
    message = str(exc)
    return "Could not connect to the endpoint URL" in message or (
        "when calling the HeadObject operation: Not Found" in message
    )
