"""Shared archive-command helpers for LocalStack integration tests."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

import pytest
import s3_archiver_cli.main as cli_module
from s3_archiver_core.archive import (
    ArchiveBucket,
    ArchiveRunLock,
    ArchiveRunResult,
    DebugLogger,
)
from s3_archiver_core.archive import (
    run_archive as run_core_archive,
)
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.s3 import S3Client
from typer.testing import CliRunner

from tests.integration.localstack_harness import (
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    localstack_test_env,
)
from tests.integration.localstack_object_helpers import localstack_s3_client

RUNNER = CliRunner()
FROZEN_ARCHIVE_RUN_STARTED_AT = datetime(2100, 1, 1, tzinfo=UTC)
_RETRYABLE_LOCALSTACK_ERRORS = (
    "Connection was closed before we received a valid response",
    "Could not connect to the endpoint URL",
)
type ArchiveSide = Literal["source", "destination"]


class ArchiveManifestPayload(TypedDict):
    object_count: int


class ArchivePhasePayload(TypedDict):
    status: str


class ArchiveCommandPayload(TypedDict):
    status: str
    source_bucket: str
    destination_bucket: str
    manifest: ArchiveManifestPayload
    phases: dict[str, ArchivePhasePayload]


def archive_env(tmp_path: Path, bucket_pair: LocalstackBucketPair) -> dict[str, str]:
    env = localstack_test_env(
        bucket_pair,
        endpoint=os.environ.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT),
        log_dir=str(tmp_path / "logs"),
    )
    env["ARCHIVER_RETENTION_DAYS"] = "1"
    env["ARCHIVER_MAX_WORKERS"] = "1"
    return env


def archive_client(env: Mapping[str, str], side: ArchiveSide) -> S3Client:
    return localstack_s3_client(env, side)


def run_archive_command(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    *,
    attempts: int = 3,
) -> ArchiveCommandPayload:
    monkeypatch.setattr(os, "environ", env)
    core_run_archive = run_core_archive

    def run_archive_with_frozen_timestamp(
        source: ArchiveBucket,
        destination: ArchiveBucket,
        options: ArchiveOptions,
        *,
        run_started_at_utc: datetime | None = None,
        run_lock: ArchiveRunLock | None = None,
        debug_logger: DebugLogger | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> ArchiveRunResult:
        _ = run_started_at_utc
        return core_run_archive(
            source,
            destination,
            options,
            run_started_at_utc=FROZEN_ARCHIVE_RUN_STARTED_AT,
            run_lock=run_lock,
            debug_logger=debug_logger,
            clock=clock,
        )

    monkeypatch.setattr(cli_module, "run_archive", run_archive_with_frozen_timestamp)
    for attempt in range(attempts):
        result = RUNNER.invoke(cli_module.app, ["archive"])
        if result.exit_code == 0 and result.stderr == "":
            json_line = next(
                line for line in reversed(result.stdout.splitlines()) if line.startswith("{")
            )
            return cast(ArchiveCommandPayload, json.loads(json_line))
        if attempt == attempts - 1 or not _is_retryable_archive_failure(result.stderr):
            assert result.exit_code == 0, result.stderr
        time.sleep(0.5)
    raise AssertionError("archive retry loop exhausted without returning")


def _is_retryable_archive_failure(stderr: str) -> bool:
    return any(message in stderr for message in _RETRYABLE_LOCALSTACK_ERRORS)
