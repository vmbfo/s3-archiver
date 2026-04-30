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
from tests.e2e.visual_demo_data import (
    DEMO_ARCHIVE_COUNT,
    DEMO_ARCHIVE_DAY_COUNT,
    DEMO_ARCHIVE_ROOT_COUNT,
    DEMO_FILES_PER_PATH_DAY,
    DEMO_RETENTION_DAYS,
    DEMO_SEEDED_OBJECT_COUNT,
    archive_demo_days,
    archive_member_name,
    expected_archive_members,
    expected_pax_headers,
    invalid_demo_keys,
    retained_demo_keys,
    sampled_archive_members,
    seed_daily_demo_objects,
    target_day_demo_cases,
)
from tests.e2e.visual_demo_terminal import run_visual_demo as render_visual_demo
from tests.integration.localstack_harness import (
    LOCALSTACK_COMPOSE_ENDPOINT,
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    compose_runtime_log_dir,
    localstack_test_env,
)
from tests.integration.localstack_object_helpers import (
    listed_keys,
    localstack_s3_client,
    read_tar_gz_member_pax_headers,
    read_tar_gz_members_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_RETRYABLE_MESSAGES = (
    "HeadBucket operation: Not Found",
    "Connection was closed before we received a valid response",
    'optional dependency "localstack" failed to start',
    "exited (137)",
    "unable to upgrade to tcp, received 409",
    "app is missing dependency localstack",
    "network s3-archiver_default not found",
    'container name "/s3-archiver-localstack-1" is already in use',
)
COMPOSE_RETRYABLE_RETURNCODES = (4, 137)
VISUAL_DEMO_RETRIES = 4
VISUAL_DEMO_RETRY_DELAY_SECONDS = 2.0


@pytest.mark.e2e()
def test_compose_demo_streams_real_bucket_story_and_finishes_with_json_summary(
    tmp_path: Path,
    compose_env: dict[str, str],
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    bucket_pair = localstack_bucket_pair
    source_client = demo_client(tmp_path, bucket_pair, "source")
    destination_client = demo_client(tmp_path, bucket_pair, "destination")
    source_prefix = "compose-demo"
    seed_now = datetime.now(tz=UTC)
    target_day = (seed_now.astimezone(UTC) - timedelta(days=DEMO_RETENTION_DAYS)).date()
    archive_days = archive_demo_days(seed_now)
    archived_keys = {
        key for day in archive_days for _, key in target_day_demo_cases(source_prefix, day)
    }
    retained_keys = set(retained_demo_keys(source_prefix, target_day))
    invalid_keys = set(invalid_demo_keys(source_prefix, target_day))
    source_keys = archived_keys | retained_keys | invalid_keys
    archive_members = expected_archive_members(source_prefix, archive_days)
    archive_keys = set(archive_members)
    seed_daily_demo_objects(
        source_client,
        bucket_pair.source,
        prefix=source_prefix,
        seed_now=seed_now,
    )
    env_file = write_demo_env_file(tmp_path, bucket_pair)
    run_env = dict(compose_env)
    run_env["APP_ENV_FILE"] = str(env_file)

    result = _run_visual_demo(run_env)
    payload = demo_payload(result.stdout)

    assert "== S3 Archiver Visual Demo ==" in result.stdout
    assert "== Archive Candidates ==" in result.stdout
    assert "== Cleanup Preview ==" in result.stdout
    assert f"archive day count: {DEMO_ARCHIVE_DAY_COUNT}" in result.stdout
    assert f"archive day range: {min(archive_days)} through {max(archive_days)}" in result.stdout
    assert f"archive root count: {DEMO_ARCHIVE_ROOT_COUNT}" in result.stdout
    assert f"archive group count: {DEMO_ARCHIVE_COUNT}" in result.stdout
    assert "source objects per archive: min=2 max=2" in result.stdout
    assert all(
        f"SKIP   key={key} reason=outside retention window" in result.stdout
        for key in retained_keys
    )
    assert all(
        f"SKIP   key={key} reason=no reliable key timestamp" in result.stdout
        for key in invalid_keys
    )
    assert payload["status"] == "ok"
    archive_manifest = cast(dict[str, object], payload["archive_manifest"])
    cleanup_preview = cast(dict[str, object], payload["cleanup_preview"])
    archive_result = cast(dict[str, object], payload["archive_result"])
    assert archive_manifest["object_count"] == len(archived_keys)
    assert archive_manifest["archive_days"] == [day.isoformat() for day in sorted(archive_days)]
    assert archive_manifest["destination_archive_keys"] == sorted(archive_keys)
    assert archive_manifest["archive_count"] == len(archive_keys)
    assert archive_manifest["skipped_object_count"] == len(retained_keys | invalid_keys)
    assert cleanup_preview["object_count"] == len(archived_keys)
    assert cleanup_preview["destination_archive_keys"] == sorted(archive_keys)
    assert cleanup_statuses(archive_result) == ["skipped"] * len(archive_keys)
    assert cleanup_statuses(cleanup_preview) == ["skipped"] * len(archive_keys)
    assert group_source_counts(archive_result) == {DEMO_FILES_PER_PATH_DAY}
    assert group_source_counts(cleanup_preview) == {DEMO_FILES_PER_PATH_DAY}
    assert payload["cleanup_preview_left_bucket_state_unchanged"] is True
    assert listed_keys(destination_client, bucket_pair.destination) == archive_keys
    for archive_key, members in sampled_archive_members(archive_members).items():
        assert read_tar_gz_members_text(
            destination_client, bucket_pair.destination, archive_key
        ) == {archive_member_name(key): f"payload for {key}\n" for key in members}
        headers = read_tar_gz_member_pax_headers(
            destination_client, bucket_pair.destination, archive_key
        )
        assert {name: values for name, values in headers.items() if values} == expected_pax_headers(
            members
        )
    assert listed_keys(source_client, bucket_pair.source) == source_keys


def write_demo_env_file(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
    *,
    cleanup_enabled: bool = False,
) -> Path:
    env = localstack_test_env(
        bucket_pair,
        endpoint=LOCALSTACK_COMPOSE_ENDPOINT,
        log_dir=compose_runtime_log_dir(bucket_pair),
    )
    env["ARCHIVER_RETENTION_DAYS"] = str(DEMO_RETENTION_DAYS)
    env["ARCHIVER_ENABLE_CLEANUP"] = "true" if cleanup_enabled else "false"
    env["ARCHIVER_MAX_WORKERS"] = "1"
    env["LOG_LEVEL"] = "WARNING"
    env_file = tmp_path / "compose-demo.env"
    _ = env_file.write_text(
        "".join(f"{key}={value}\n" for key, value in sorted(env.items())),
        encoding="utf-8",
    )
    return env_file


def demo_client(
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


def run_demo_compose(
    env: dict[str, str],
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return run_compose(
        env,
        *args,
        check=check,
        retryable_messages=COMPOSE_RETRYABLE_MESSAGES,
        retryable_returncodes=COMPOSE_RETRYABLE_RETURNCODES,
    )


def _run_visual_demo(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return render_visual_demo(
        env,
        repo_root=REPO_ROOT,
        compose_runner=run_demo_compose,
        retryable_messages=COMPOSE_RETRYABLE_MESSAGES,
        retryable_returncodes=COMPOSE_RETRYABLE_RETURNCODES,
        retries=VISUAL_DEMO_RETRIES,
        retry_delay_seconds=VISUAL_DEMO_RETRY_DELAY_SECONDS,
        retention_days=DEMO_RETENTION_DAYS,
        seeded_count=DEMO_SEEDED_OBJECT_COUNT,
    )


def demo_payload(output: str) -> dict[str, object]:
    json_line = next(line for line in reversed(output.splitlines()) if line.startswith("{"))
    return cast(dict[str, object], json.loads(json_line))


def cleanup_statuses(payload: dict[str, object]) -> list[str]:
    groups = cast(list[dict[str, object]], payload["archive_groups"])
    return [str(group["cleanup_status"]) for group in groups]


def group_source_counts(payload: dict[str, object]) -> set[int]:
    groups = cast(list[dict[str, object]], payload["archive_groups"])
    return {int(cast(int, group["source_object_count"])) for group in groups}
