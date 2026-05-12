"""Shared e2e helpers for compose archive tests."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from s3_archiver_core.s3 import S3Client
from s3_archiver_localstack_support.compose import run_app_compose
from s3_archiver_localstack_support.harness import (
    LOCALSTACK_COMPOSE_ENDPOINT,
    LocalstackBucketPair,
    compose_runtime_log_dir,
    localstack_test_env,
    write_localstack_env_file,
)
from s3_archiver_localstack_support.objects import localstack_s3_client


def write_archive_env_file(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
    *,
    overrides: Mapping[str, str] | None = None,
) -> Path:
    """Write the compose app env file for archive e2e tests."""

    return write_localstack_env_file(
        tmp_path,
        bucket_pair,
        endpoint=LOCALSTACK_COMPOSE_ENDPOINT,
        log_dir=compose_runtime_log_dir(bucket_pair),
        overrides=overrides,
    )


def compose_archive_client(
    tmp_path: Path,
    compose_env: dict[str, str],
    bucket_pair: LocalstackBucketPair,
    side: Literal["source", "destination"],
) -> S3Client:
    """Build a host-side LocalStack client for a compose archive test bucket."""

    env = localstack_test_env(
        bucket_pair,
        endpoint=compose_env["LOCALSTACK_S3_URL"],
        log_dir=str(tmp_path / "host-logs"),
    )
    return localstack_s3_client(env, side)


def run_archive_compose(
    env: dict[str, str], *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run Docker Compose with archive e2e retry handling."""

    return run_app_compose(env, *args, check=check)
