"""Compose e2e coverage for the human-readable visual demo command."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import pytest
from s3_archiver_core.s3 import S3Client

from tests.e2e.compose_helpers import run_compose
from tests.e2e.visual_demo_terminal import print_verified_summary
from tests.e2e.visual_demo_terminal import run_visual_demo as render_visual_demo
from tests.integration.localstack_harness import (
    LOCALSTACK_COMPOSE_ENDPOINT,
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    compose_runtime_log_dir,
    localstack_test_env,
)
from tests.integration.localstack_object_helpers import (
    CANONICAL_RETENTION_DATASET_DAYS,
    listed_keys,
    localstack_s3_client,
    put_test_object,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_RETENTION_DAYS = 60
_COMPOSE_RETRYABLE_MESSAGES = (
    "HeadBucket operation: Not Found",
    "Connection was closed before we received a valid response",
    'optional dependency "localstack" failed to start',
    "exited (137)",
    "unable to upgrade to tcp, received 409",
    "app is missing dependency localstack",
    "network s3-archiver_default not found",
    'container name "/s3-archiver-localstack-1" is already in use',
)
_COMPOSE_RETRYABLE_RETURNCODES = (4, 137)
_VISUAL_DEMO_RETRIES = 4
_VISUAL_DEMO_RETRY_DELAY_SECONDS = 2.0


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
    seed_now = datetime.now(tz=UTC)
    daily_keys = _daily_demo_keys(source_prefix, seed_now=seed_now)
    source_keys = set(daily_keys.values())
    archived_days = {DEMO_RETENTION_DAYS}
    archived_keys = {daily_keys[day] for day in archived_days}
    retained_keys = source_keys - archived_keys
    target_day = (seed_now.astimezone(UTC) - timedelta(days=DEMO_RETENTION_DAYS)).date()
    archive_keys = {f"{source_prefix}/{target_day}.tar.gz"}
    _seed_daily_demo_objects(
        source_client,
        bucket_pair.source,
        prefix=source_prefix,
        seed_now=seed_now,
    )
    env_file = _write_demo_env_file(tmp_path, bucket_pair)
    run_env = dict(compose_env)
    run_env["APP_ENV_FILE"] = str(env_file)

    result = _run_visual_demo(run_env)
    payload = _demo_payload(result.stdout)

    assert "== S3 Archiver Visual Demo ==" in result.stdout
    assert "== Before archive ==" in result.stdout
    assert "== Archive Candidates ==" in result.stdout
    assert "== Cleanup Preview ==" in result.stdout
    assert all(f"SOURCE key={key}" in result.stdout for key in source_keys)
    assert f"GROUP  target_day={target_day}" in result.stdout
    assert all(f"destination_archive_key={key}" in result.stdout for key in archive_keys)
    assert all(
        f"SKIP   key={key} reason=outside target day" in result.stdout for key in retained_keys
    )
    assert "COPY   key=" not in result.stdout
    assert all(f"DELETE key={key}" in result.stdout for key in archived_keys)
    assert not any(f"DELETE key={key}" in result.stdout for key in retained_keys)
    assert payload["status"] == "ok"
    archive_manifest = cast(dict[str, object], payload["archive_manifest"])
    cleanup_preview = cast(dict[str, object], payload["cleanup_preview"])
    archive_result = cast(dict[str, object], payload["archive_result"])
    assert archive_manifest["object_count"] == len(archived_keys)
    assert archive_manifest["destination_archive_keys"] == sorted(archive_keys)
    assert archive_manifest["archive_count"] == len(archive_keys)
    assert cleanup_preview["object_count"] == len(archived_keys)
    assert cleanup_preview["destination_archive_keys"] == sorted(archive_keys)
    assert _cleanup_statuses(archive_result) == ["skipped"]
    assert _cleanup_statuses(cleanup_preview) == ["skipped"]
    assert payload["cleanup_preview_left_bucket_state_unchanged"] is True
    assert listed_keys(destination_client, bucket_pair.destination) == archive_keys
    assert listed_keys(source_client, bucket_pair.source) == source_keys
    print_verified_summary(
        payload,
        total_count=len(source_keys),
        copied_count=len(archived_keys),
        retained_count=len(retained_keys),
    )


def _seed_daily_demo_objects(
    client: S3Client,
    bucket: str,
    *,
    prefix: str,
    seed_now: datetime,
) -> None:
    seeded_now = seed_now.astimezone(UTC).replace(microsecond=0)
    for day, key in _daily_demo_keys(prefix, seed_now=seeded_now).items():
        target = seeded_now - timedelta(days=day)
        _ = put_test_object(
            client,
            bucket,
            key,
            metadata={
                "s3-archiver-test-age-days": str(day),
                "s3-archiver-test-last-modified": target.isoformat(),
            },
        )


def _daily_demo_keys(prefix: str, *, seed_now: datetime) -> dict[int, str]:
    seeded_now = seed_now.astimezone(UTC).replace(microsecond=0)
    return {
        day: f"{prefix}/{(seeded_now - timedelta(days=day)).date().isoformat()}.txt"
        for day in CANONICAL_RETENTION_DATASET_DAYS
    }


def _write_demo_env_file(tmp_path: Path, bucket_pair: LocalstackBucketPair) -> Path:
    env = localstack_test_env(
        bucket_pair,
        endpoint=LOCALSTACK_COMPOSE_ENDPOINT,
        log_dir=compose_runtime_log_dir(bucket_pair),
    )
    env["ARCHIVER_RETENTION_DAYS"] = str(DEMO_RETENTION_DAYS)
    env["ARCHIVER_ENABLE_CLEANUP"] = "false"
    env["ARCHIVER_MAX_WORKERS"] = "1"
    env["LOG_LEVEL"] = "WARNING"
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


def _run_compose(
    env: dict[str, str],
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_compose(
        env,
        *args,
        check=check,
        retryable_messages=_COMPOSE_RETRYABLE_MESSAGES,
        retryable_returncodes=_COMPOSE_RETRYABLE_RETURNCODES,
    )


def _run_visual_demo(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return render_visual_demo(
        env,
        repo_root=REPO_ROOT,
        compose_runner=_run_compose,
        retryable_messages=_COMPOSE_RETRYABLE_MESSAGES,
        retryable_returncodes=_COMPOSE_RETRYABLE_RETURNCODES,
        retries=_VISUAL_DEMO_RETRIES,
        retry_delay_seconds=_VISUAL_DEMO_RETRY_DELAY_SECONDS,
        retention_days=DEMO_RETENTION_DAYS,
        seeded_count=len(CANONICAL_RETENTION_DATASET_DAYS),
    )


def _demo_payload(output: str) -> dict[str, object]:
    json_line = next(line for line in reversed(output.splitlines()) if line.startswith("{"))
    return cast(dict[str, object], json.loads(json_line))


def _cleanup_statuses(payload: dict[str, object]) -> list[str]:
    groups = cast(list[dict[str, object]], payload["archive_groups"])
    return [str(group["cleanup_status"]) for group in groups]
