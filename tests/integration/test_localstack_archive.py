"""Archive command integration tests against isolated LocalStack buckets."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict, cast

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
from s3_archiver_core.s3 import S3Client, build_s3_client
from s3_archiver_core.settings import AppSettings
from typer.testing import CliRunner

from tests.integration.localstack_harness import (
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    localstack_test_env,
)

RUNNER = CliRunner()
FROZEN_ARCHIVE_RUN_STARTED_AT = datetime(2100, 1, 1, tzinfo=UTC)


class ArchivePayload(TypedDict):
    status: str
    source_bucket: str
    destination_bucket: str
    manifest: dict[str, object]
    phases: dict[str, dict[str, object]]
    key: NotRequired[str | None]


@pytest.mark.integration()
@pytest.mark.parametrize("cleanup_value", [None, "false", "true"])
def test_archive_command_copies_isolated_localstack_keys_and_honors_cleanup_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
    cleanup_value: str | None,
) -> None:
    env = _archive_env(tmp_path, localstack_bucket_pair)
    if cleanup_value is None:
        del env["ARCHIVER_ENABLE_CLEANUP"]
    else:
        env["ARCHIVER_ENABLE_CLEANUP"] = cleanup_value
    source_client = _client(env, "source")
    destination_client = _client(env, "destination")
    source_keys = {"archive/a.txt", "archive/b.txt"}
    _put_source_objects(source_client, localstack_bucket_pair.source, source_keys)

    payload = _run_archive(monkeypatch, env)

    assert payload["status"] == "ok"
    assert payload["source_bucket"] == localstack_bucket_pair.source
    assert payload["destination_bucket"] == localstack_bucket_pair.destination
    assert payload["manifest"]["object_count"] == len(source_keys)
    expected_cleanup_status = "ok" if cleanup_value == "true" else "skipped"
    assert _phase_statuses(payload) == {
        "list": "ok",
        "copy": "ok",
        "verify": "ok",
        "cleanup": expected_cleanup_status,
    }
    assert _listed_keys(destination_client, localstack_bucket_pair.destination) == source_keys
    expected_source_keys: set[str] = set() if cleanup_value == "true" else source_keys
    assert _listed_keys(source_client, localstack_bucket_pair.source) == expected_source_keys


@pytest.mark.integration()
def test_archive_command_whitelist_filter_controls_copy_and_cleanup_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    env = _archive_env(tmp_path, localstack_bucket_pair)
    env["ARCHIVER_ENABLE_CLEANUP"] = "true"
    env["S3_SOURCE_PATH_WHITELIST_ENABLED"] = "true"
    env["S3_SOURCE_PATH_WHITELIST"] = json.dumps(["include/"])
    source_client = _client(env, "source")
    destination_client = _client(env, "destination")
    _put_source_objects(
        source_client,
        localstack_bucket_pair.source,
        {"include/a.txt", "include/nested/b.txt", "exclude/c.txt"},
    )

    payload = _run_archive(monkeypatch, env)

    assert payload["status"] == "ok"
    assert payload["manifest"]["object_count"] == 2
    assert _listed_keys(destination_client, localstack_bucket_pair.destination) == {
        "include/a.txt",
        "include/nested/b.txt",
    }
    assert _listed_keys(source_client, localstack_bucket_pair.source) == {"exclude/c.txt"}


@pytest.mark.integration()
def test_archive_command_blacklist_filter_controls_copy_and_cleanup_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    env = _archive_env(tmp_path, localstack_bucket_pair)
    env["ARCHIVER_ENABLE_CLEANUP"] = "true"
    env["S3_SOURCE_PATH_BLACKLIST_ENABLED"] = "true"
    env["S3_SOURCE_PATH_BLACKLIST"] = json.dumps(["blocked/"])
    source_client = _client(env, "source")
    destination_client = _client(env, "destination")
    _put_source_objects(
        source_client,
        localstack_bucket_pair.source,
        {"allowed/a.txt", "blocked/b.txt", "blocked/nested/c.txt"},
    )

    payload = _run_archive(monkeypatch, env)

    assert payload["status"] == "ok"
    assert payload["manifest"]["object_count"] == 1
    assert _listed_keys(destination_client, localstack_bucket_pair.destination) == {"allowed/a.txt"}
    assert _listed_keys(source_client, localstack_bucket_pair.source) == {
        "blocked/b.txt",
        "blocked/nested/c.txt",
    }


def _archive_env(tmp_path: Path, bucket_pair: LocalstackBucketPair) -> dict[str, str]:
    env = localstack_test_env(
        bucket_pair,
        endpoint=os.environ.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT),
        log_dir=str(tmp_path / "logs"),
    )
    env["ARCHIVER_RETENTION_DAYS"] = "1"
    env["ARCHIVER_MAX_WORKERS"] = "1"
    return env


def _run_archive(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> ArchivePayload:
    monkeypatch.setattr(os, "environ", env)

    def run_archive_with_frozen_timestamp(
        source: ArchiveBucket,
        destination: ArchiveBucket,
        options: ArchiveOptions,
        *,
        run_lock: ArchiveRunLock | None = None,
        debug_logger: DebugLogger | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> ArchiveRunResult:
        return run_core_archive(
            source,
            destination,
            options,
            run_started_at_utc=FROZEN_ARCHIVE_RUN_STARTED_AT,
            run_lock=run_lock,
            debug_logger=debug_logger,
            clock=clock,
        )

    monkeypatch.setattr(cli_module, "run_archive", run_archive_with_frozen_timestamp)
    result = RUNNER.invoke(cli_module.app, ["archive"])

    assert result.exit_code == 0, result.stderr
    assert result.stderr == ""
    json_line = next(line for line in reversed(result.stdout.splitlines()) if line.startswith("{"))
    return cast(ArchivePayload, json.loads(json_line))


def _client(env: Mapping[str, str], side: str) -> S3Client:
    settings = AppSettings.from_env(env)
    if side == "source":
        return build_s3_client(settings.source)
    if side == "destination":
        return build_s3_client(settings.destination)
    raise ValueError(f"unknown S3 side {side!r}")


def _put_source_objects(client: S3Client, bucket: str, keys: set[str]) -> None:
    for key in sorted(keys):
        _ = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=f"payload for {key}\n".encode(),
            ContentType="text/plain",
            Metadata={"seed-key": key},
        )


def _listed_keys(client: S3Client, bucket: str) -> set[str]:
    response = client.list_objects_v2(Bucket=bucket)
    contents = response.get("Contents")
    if not isinstance(contents, list):
        return set()
    keys: set[str] = set()
    for raw_entry in cast(list[object], contents):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(dict[str, object], raw_entry)
        key = entry.get("Key")
        if isinstance(key, str):
            keys.add(key)
    return keys


def _phase_statuses(payload: ArchivePayload) -> dict[str, object]:
    return {
        name: phase["status"]
        for name, phase in payload["phases"].items()
        if name in {"list", "copy", "verify", "cleanup"}
    }
