"""Compose e2e coverage for the human-readable visual demo command."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest
from s3_archiver_core.s3 import S3Client

from tests.e2e.compose_helpers import run_compose
from tests.integration.localstack_harness import (
    LOCALSTACK_COMPOSE_ENDPOINT,
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    localstack_test_env,
)
from tests.integration.localstack_object_helpers import (
    listed_keys,
    localstack_s3_client,
    seed_timestamped_objects,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE_RETRYABLE_MESSAGES = (
    "HeadBucket operation: Not Found",
    "Connection was closed before we received a valid response",
    "unable to upgrade to tcp, received 409",
)


@pytest.mark.e2e()
def test_compose_demo_streams_real_bucket_story_and_finishes_with_json_summary(
    tmp_path: Path,
    compose_env: dict[str, str],
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    bucket_pair = localstack_bucket_pair
    source_client = _client(tmp_path, bucket_pair, "source")
    destination_client = _client(tmp_path, bucket_pair, "destination")
    source_prefix = "compose-demo"
    source_keys = {
        f"{source_prefix}/age-2-days.txt",
        f"{source_prefix}/age-3-days.txt",
    }
    seed_timestamped_objects(
        source_client,
        bucket_pair.source,
        prefix=source_prefix,
        days=(2, 3),
        seed_now=datetime.now(tz=UTC),
    )
    env_file = _write_demo_env_file(tmp_path, bucket_pair)
    run_env = dict(compose_env)
    run_env["APP_ENV_FILE"] = str(env_file)

    result = _run_compose(run_env, "run", "--rm", "app", "demo")
    payload = _demo_payload(result.stdout)

    assert "== S3 Archiver Visual Demo ==" in result.stdout
    assert "== Before archive ==" in result.stdout
    assert "== Archive Candidates ==" in result.stdout
    assert "== Cleanup Preview ==" in result.stdout
    assert any(f"COPY   key={key}" in result.stdout for key in source_keys)
    assert any(f"DELETE key={key}" in result.stdout for key in source_keys)
    assert payload["status"] == "ok"
    archive_manifest = cast(dict[str, object], payload["archive_manifest"])
    cleanup_preview = cast(dict[str, object], payload["cleanup_preview"])
    assert archive_manifest["object_count"] == len(source_keys)
    assert cleanup_preview["object_count"] == len(source_keys)
    assert payload["cleanup_preview_left_bucket_state_unchanged"] is True
    assert listed_keys(destination_client, bucket_pair.destination) == source_keys
    assert listed_keys(source_client, bucket_pair.source) == source_keys


def _write_demo_env_file(tmp_path: Path, bucket_pair: LocalstackBucketPair) -> Path:
    env = localstack_test_env(
        bucket_pair,
        endpoint=LOCALSTACK_COMPOSE_ENDPOINT,
        log_dir="/var/log/s3-archiver",
    )
    env["ARCHIVER_RETENTION_DAYS"] = "1"
    env["ARCHIVER_ENABLE_CLEANUP"] = "false"
    env["ARCHIVER_MAX_WORKERS"] = "1"
    env_file = tmp_path / "compose-demo.env"
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
    return localstack_s3_client(env, side)


def _run_compose(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return run_compose(env, *args, retryable_messages=_COMPOSE_RETRYABLE_MESSAGES)


def _demo_payload(output: str) -> dict[str, object]:
    json_line = next(line for line in reversed(output.splitlines()) if line.startswith("{"))
    return cast(dict[str, object], json.loads(json_line))
