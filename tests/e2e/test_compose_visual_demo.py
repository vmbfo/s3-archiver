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
    DEMO_ARCHIVE_START_AGE_DAYS,
    DEMO_DIRECT_COPY_COUNT,
    DEMO_FILES_PER_PATH_DAY,
    DEMO_SEEDED_OBJECT_COUNT,
    archive_demo_days,
    archive_member_name,
    demo_config_json,
    expected_archive_members,
    expected_direct_destination_keys,
    expected_pax_headers,
    invalid_demo_keys,
    newer_demo_keys,
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
    read_object_text,
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
    target_day = (seed_now.astimezone(UTC) - timedelta(days=DEMO_ARCHIVE_START_AGE_DAYS)).date()
    archive_days = archive_demo_days(seed_now)
    demo_cases = [
        case for day in archive_days for case in target_day_demo_cases(source_prefix, day)
    ]
    eligible_keys = {case.key for case in demo_cases}
    newer_keys = set(newer_demo_keys(source_prefix, target_day))
    invalid_keys = set(invalid_demo_keys(source_prefix, target_day))
    source_keys = eligible_keys | newer_keys | invalid_keys
    archive_members = expected_archive_members(source_prefix, archive_days)
    archive_keys = set(archive_members)
    direct_keys = expected_direct_destination_keys(source_prefix, archive_days)
    direct_source_by_destination = {
        case.destination_key: case.key for case in demo_cases if case.route.copy_mode == "direct"
    }
    seed_daily_demo_objects(
        source_client,
        bucket_pair.source,
        prefix=source_prefix,
        seed_now=seed_now,
    )
    env_file = write_demo_env_file(tmp_path, bucket_pair)
    run_env = dict(compose_env)
    run_env["APP_ENV_FILE"] = str(env_file)
    run_env["ARCHIVER_CONFIG_JSON"] = demo_config_json(bucket_pair, prefix=source_prefix)

    result = _run_visual_demo(run_env)
    payload = demo_payload(result.stdout)

    assert "== S3 Archiver Visual Demo ==" in result.stdout
    assert "== Archive Candidates ==" in result.stdout
    assert "== Cleanup Preview ==" not in result.stdout
    assert f"archive day count: {DEMO_ARCHIVE_DAY_COUNT}" in result.stdout
    assert f"archive day range: {min(archive_days)} through {max(archive_days)}" in result.stdout
    assert f"archive root count: {DEMO_ARCHIVE_ROOT_COUNT}" in result.stdout
    assert f"archive group count: {DEMO_ARCHIVE_COUNT}" in result.stdout
    assert f"direct copy count: {DEMO_DIRECT_COPY_COUNT}" in result.stdout
    assert "source objects per archive: min=2 max=2" in result.stdout
    for detail in (
        "route=direct-daily parser=direct copy_mode=daily_tar_gz",
        "route=direct-copy parser=direct copy_mode=direct",
        "route=filename-daily parser=filename_timestamp copy_mode=daily_tar_gz",
        "route=filename-copy parser=filename_timestamp copy_mode=direct",
        "route=folder-daily parser=folder_timestamp copy_mode=daily_tar_gz",
        "route=folder-copy parser=folder_timestamp copy_mode=direct",
    ):
        assert detail in result.stdout
    assert all(
        f"SKIP   key={key} reason=parser timestamp after run start" in result.stdout
        for key in newer_keys
    )
    filename_skip = f"SKIP   key={source_prefix}/filename/daily/skips/no-timestamp-latest.txt"
    folder_skip = f"SKIP   key={source_prefix}/folder/daily/skips/no-folder-timestamp.txt"
    assert f"{filename_skip} reason=no reliable key timestamp" in result.stdout
    assert f"{folder_skip} reason=no reliable folder timestamp" in result.stdout
    assert payload["status"] == "ok"
    archive_manifest = cast(dict[str, object], payload["archive_manifest"])
    archive_result = cast(dict[str, object], payload["archive_result"])
    assert archive_manifest["object_count"] == len(eligible_keys)
    assert archive_manifest["archive_days"] == [day.isoformat() for day in sorted(archive_days)]
    assert archive_manifest["destination_archive_keys"] == sorted(archive_keys)
    assert set(cast(list[str], archive_manifest["destination_keys"])) == archive_keys | direct_keys
    assert archive_manifest["archive_count"] == len(archive_keys)
    assert archive_manifest["direct_copy_count"] == len(direct_keys)
    assert archive_manifest["skipped_object_count"] == len(newer_keys | invalid_keys)
    assert archive_result["direct_copy_count"] == len(direct_keys)
    assert "cleanup_preview" not in payload
    assert all("cleanup_status" not in group for group in archive_groups(archive_result))
    assert group_source_counts(archive_result) == {DEMO_FILES_PER_PATH_DAY}
    assert listed_keys(destination_client, bucket_pair.destination) == archive_keys | direct_keys
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
    for destination_key in _sampled_keys(direct_keys):
        source_key = direct_source_by_destination[destination_key]
        assert read_object_text(destination_client, bucket_pair.destination, destination_key) == (
            f"payload for {source_key}\n"
        )
    assert listed_keys(source_client, bucket_pair.source) == source_keys


def write_demo_env_file(
    tmp_path: Path,
    bucket_pair: LocalstackBucketPair,
) -> Path:
    env = localstack_test_env(
        bucket_pair,
        endpoint=LOCALSTACK_COMPOSE_ENDPOINT,
        log_dir=compose_runtime_log_dir(bucket_pair),
    )
    env["ARCHIVER_CONFIG_JSON"] = demo_config_json(bucket_pair, prefix="compose-demo")
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
        archive_start_age_days=DEMO_ARCHIVE_START_AGE_DAYS,
        seeded_count=DEMO_SEEDED_OBJECT_COUNT,
    )


def demo_payload(output: str) -> dict[str, object]:
    json_line = next(line for line in reversed(output.splitlines()) if line.startswith("{"))
    return cast(dict[str, object], json.loads(json_line))


def archive_groups(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], payload["archive_groups"])


def group_source_counts(payload: dict[str, object]) -> set[int]:
    return {int(cast(int, group["source_object_count"])) for group in archive_groups(payload)}


def _sampled_keys(keys: set[str]) -> tuple[str, str, str]:
    sorted_keys = sorted(keys)
    return sorted_keys[0], sorted_keys[len(sorted_keys) // 2], sorted_keys[-1]
