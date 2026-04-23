"""End-to-end tests for archive runs through the Docker Compose app service."""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypedDict, cast

import pytest
from s3_archiver_core.s3 import S3Client, build_s3_client
from s3_archiver_core.settings import AppSettings

from tests.integration.localstack_harness import (
    LOCALSTACK_COMPOSE_ENDPOINT,
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    localstack_test_env,
)
from tests.integration.test_localstack_timestamp_seed import run_timestamp_seed_helper

_COMPOSE_RETRY_DELAY_SECONDS = 2.0
_COMPOSE_RUN_RETRIES = 4
REPO_ROOT = Path(__file__).resolve().parents[2]


class ArchivePhasePayload(TypedDict):
    status: str


class ArchivePayload(TypedDict):
    status: str
    source_bucket: str
    destination_bucket: str
    manifest: dict[str, object]
    phases: dict[str, ArchivePhasePayload]


@pytest.mark.e2e()
@pytest.mark.parametrize(
    ("cleanup_value", "expected_cleanup_status", "source_should_remain"),
    [
        (None, "skipped", True),
        ("false", "skipped", True),
        ("true", "ok", False),
    ],
)
def test_compose_archive_copies_keys_and_honors_cleanup_gate(
    tmp_path: Path,
    compose_env: dict[str, str],
    localstack_bucket_pair: LocalstackBucketPair,
    cleanup_value: str | None,
    expected_cleanup_status: str,
    source_should_remain: bool,
) -> None:
    bucket_pair = localstack_bucket_pair
    source_client = _client(tmp_path, bucket_pair, "source")
    destination_client = _client(tmp_path, bucket_pair, "destination")
    source_prefix = _case_source_prefix(cleanup_value)
    source_keys = _case_source_keys(source_prefix)
    _ = run_timestamp_seed_helper(
        compose_env,
        prefix=source_prefix,
        days=(2, 3),
        seed_now=datetime.now(tz=UTC).replace(microsecond=0),
    )
    assert _listed_keys(source_client, bucket_pair.source) == source_keys
    env_file = _write_archive_env_file(tmp_path, bucket_pair, cleanup_value)
    run_env = dict(compose_env)
    run_env["APP_ENV_FILE"] = str(env_file)

    result = _run_compose(run_env, "run", "--rm", "app", "archive")
    payload = _archive_payload(result.stdout)

    assert payload["status"] == "ok"
    assert payload["source_bucket"] == bucket_pair.source
    assert payload["destination_bucket"] == bucket_pair.destination
    assert payload["manifest"]["object_count"] == len(source_keys)
    assert _phase_statuses(payload) == {
        "list": "ok",
        "copy": "ok",
        "verify": "ok",
        "cleanup": expected_cleanup_status,
    }
    assert _listed_keys(destination_client, bucket_pair.destination) == source_keys
    expected_source_keys = source_keys if source_should_remain else set[str]()
    assert _listed_keys(source_client, bucket_pair.source) == expected_source_keys


def _case_source_prefix(cleanup_value: str | None) -> str:
    return f"compose-archive/{_case_name(cleanup_value)}"


def _case_name(cleanup_value: str | None) -> str:
    if cleanup_value is None:
        return "unset"
    return cleanup_value


def _case_source_keys(prefix: str) -> set[str]:
    return {
        f"{prefix}/age-2-days.txt",
        f"{prefix}/age-3-days.txt",
    }


def _write_archive_env_file(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
    cleanup_value: str | None,
) -> Path:
    env = localstack_test_env(
        bucket_pair,
        endpoint=LOCALSTACK_COMPOSE_ENDPOINT,
        log_dir="/var/log/s3-archiver",
    )
    env["ARCHIVER_RETENTION_DAYS"] = "1"
    env["ARCHIVER_MAX_WORKERS"] = "1"
    if cleanup_value is None:
        del env["ARCHIVER_ENABLE_CLEANUP"]
    else:
        env["ARCHIVER_ENABLE_CLEANUP"] = cleanup_value
    env_file = tmp_path / "compose-archive.env"
    _ = env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(env.items())),
        encoding="utf-8",
    )
    return env_file


def _client(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
    side: Literal["source", "destination"],
) -> S3Client:
    env = localstack_test_env(
        bucket_pair,
        endpoint=LOCALSTACK_HOST_ENDPOINT,
        log_dir=str(tmp_path / "host-logs"),
    )
    settings = AppSettings.from_env(env)
    if side == "source":
        return build_s3_client(settings.source)
    return build_s3_client(settings.destination)


def _listed_keys(client: S3Client, bucket: str) -> set[str]:
    response = client.list_objects_v2(Bucket=bucket)
    contents = response.get("Contents")
    if not isinstance(contents, list):
        return set()
    keys: set[str] = set()
    for raw_entry in cast(list[object], contents):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(Mapping[str, object], raw_entry)
        key = entry.get("Key")
        if isinstance(key, str):
            keys.add(key)
    return keys


def _run_compose(
    env: dict[str, str], *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--profile", "test", *args]
    for attempt in range(_COMPOSE_RUN_RETRIES + 1):
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result
        if not check:
            return result
        if attempt == _COMPOSE_RUN_RETRIES or _is_non_retryable_compose_error(result):
            error = subprocess.CalledProcessError(
                result.returncode,
                command,
                output=result.stdout,
                stderr=result.stderr,
            )
            message = "\n".join(
                (
                    f"compose command failed with exit code {result.returncode}: {command}",
                    f"stdout:\n{result.stdout}",
                    f"stderr:\n{result.stderr}",
                )
            )
            raise AssertionError(message) from error
        time.sleep(_COMPOSE_RETRY_DELAY_SECONDS)
    raise AssertionError("compose retry loop exhausted without returning")


def _is_non_retryable_compose_error(result: subprocess.CompletedProcess[str]) -> bool:
    retryable_messages = (
        "No such container",
        "marked for removal",
        "HeadBucket operation: Not Found",
        'Could not connect to the endpoint URL: "http://localstack:4566/',
    )
    return not any(
        message in result.stderr or message in result.stdout for message in retryable_messages
    )


def _archive_payload(output: str) -> ArchivePayload:
    json_line = next(line for line in reversed(output.splitlines()) if line.startswith("{"))
    return cast(ArchivePayload, json.loads(json_line))


def _phase_statuses(payload: ArchivePayload) -> dict[str, str]:
    return {
        name: phase["status"]
        for name, phase in payload["phases"].items()
        if name in {"list", "copy", "verify", "cleanup"}
    }
