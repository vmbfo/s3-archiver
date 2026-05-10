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
from s3_archiver_core._archive_protocols import ArchiveRunLock
from s3_archiver_core.archive import ArchiveRoute, ArchiveRunResult
from s3_archiver_core.archive import run_archive_routes as run_core_archive_routes
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
FROZEN_ARCHIVE_RUN_STARTED_AT = datetime(2099, 12, 31, 12, tzinfo=UTC)
_RETRYABLE_LOCALSTACK_ERRORS = (
    "Connection was closed before we received a valid response",
    "Could not connect to the endpoint URL",
    "when calling the HeadBucket operation: Not Found",
    "when calling the ListObjectVersions operation: The specified bucket does not exist",
)
type ArchiveSide = Literal["source", "destination"]
type DebugLogger = Callable[[object, str], None]
type JsonObject = dict[str, object]


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
    return localstack_test_env(
        bucket_pair,
        endpoint=os.environ.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT),
        log_dir=str(tmp_path / "logs"),
    )


def update_single_route_config(
    env: dict[str, str],
    *,
    name: str | None = None,
    parser: str | None = None,
    copy_mode: str | None = None,
    source_path: str | None = None,
    destination_path: str | None = None,
) -> None:
    routes = cast(list[JsonObject], json.loads(env["ARCHIVER_CONFIG_JSON"]))
    route = routes[0]
    if name is not None:
        route["name"] = name
    if parser is not None:
        route["parser"] = parser
    if copy_mode is not None:
        route["copy_mode"] = copy_mode
    source = cast(JsonObject, route["source"])
    destination = cast(JsonObject, route["destination"])
    if source_path is not None:
        source["path"] = source_path
    if destination_path is not None:
        destination["path"] = destination_path
    env["ARCHIVER_CONFIG_JSON"] = json.dumps(routes)


def archive_client(env: Mapping[str, str], side: ArchiveSide) -> S3Client:
    return localstack_s3_client(env, side)


def run_archive_command(
    monkeypatch: pytest.MonkeyPatch,
    env: dict[str, str],
    *,
    attempts: int = 3,
) -> ArchiveCommandPayload:
    monkeypatch.setattr(os, "environ", env)
    core_run_archive_routes = run_core_archive_routes

    def run_archive_routes_with_frozen_timestamp(
        routes: tuple[ArchiveRoute, ...],
        options: ArchiveOptions,
        *,
        run_started_at_utc: datetime | None = None,
        run_lock: ArchiveRunLock | None = None,
        debug_logger: DebugLogger | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> ArchiveRunResult:
        _ = (run_started_at_utc, run_lock)
        return core_run_archive_routes(
            routes,
            options,
            run_started_at_utc=FROZEN_ARCHIVE_RUN_STARTED_AT,
            debug_logger=debug_logger,
            clock=clock,
        )

    monkeypatch.setattr(cli_module, "run_archive_routes", run_archive_routes_with_frozen_timestamp)
    for attempt in range(attempts):
        result = RUNNER.invoke(cli_module.app, ["archive-once"])
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
