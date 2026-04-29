"""Compose e2e coverage for the human-readable visual demo command."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import pytest
from s3_archiver_core.archive_tar import ORIGINAL_KEY_PAX_HEADER
from s3_archiver_core.s3 import S3Client

from tests.e2e.compose_helpers import run_compose
from tests.e2e.visual_demo_summary import print_verified_summary
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
    put_test_object,
    read_tar_gz_member_pax_headers,
    read_tar_gz_members_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_RETENTION_DAYS = 60
DEMO_SEEDED_OBJECT_COUNT = 16
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
    archived_keys = {key for _, key in target_day_demo_cases(source_prefix, target_day)}
    retained_keys = set(retained_demo_keys(source_prefix, target_day))
    invalid_keys = set(invalid_demo_keys(source_prefix, target_day))
    source_keys = archived_keys | retained_keys | invalid_keys
    archive_members = expected_archive_members(source_prefix, target_day)
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
    assert "== Before archive ==" in result.stdout
    assert "== Archive Candidates ==" in result.stdout
    assert "== Cleanup Preview ==" in result.stdout
    assert all(f"SOURCE key={key}" in result.stdout for key in source_keys)
    assert f"GROUP  target_day={target_day}" in result.stdout
    assert all(f"destination_archive_key={key}" in result.stdout for key in archive_keys)
    assert all(
        f"SKIP   key={key} reason=outside target day" in result.stdout for key in retained_keys
    )
    assert all(
        f"SKIP   key={key} reason=no reliable key timestamp" in result.stdout
        for key in invalid_keys
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
    assert archive_manifest["skipped_object_count"] == len(retained_keys | invalid_keys)
    assert cleanup_preview["object_count"] == len(archived_keys)
    assert cleanup_preview["destination_archive_keys"] == sorted(archive_keys)
    assert cleanup_statuses(archive_result) == ["skipped"] * len(archive_keys)
    assert cleanup_statuses(cleanup_preview) == ["skipped"] * len(archive_keys)
    assert payload["cleanup_preview_left_bucket_state_unchanged"] is True
    assert listed_keys(destination_client, bucket_pair.destination) == archive_keys
    for archive_key, source_key in archive_members.items():
        member_name = archive_member_name(source_key)
        assert read_tar_gz_members_text(
            destination_client, bucket_pair.destination, archive_key
        ) == {member_name: f"payload for {source_key}\n"}
        if member_name != source_key:
            assert read_tar_gz_member_pax_headers(
                destination_client, bucket_pair.destination, archive_key
            ) == {member_name: {ORIGINAL_KEY_PAX_HEADER: source_key}}
    assert listed_keys(source_client, bucket_pair.source) == source_keys
    print_verified_summary(
        payload,
        total_count=len(source_keys),
        copied_count=len(archived_keys),
        remaining_source_count=len(retained_keys | invalid_keys),
    )


def seed_daily_demo_objects(
    client: S3Client,
    bucket: str,
    *,
    prefix: str,
    seed_now: datetime,
) -> None:
    seeded_now = seed_now.astimezone(UTC).replace(microsecond=0)
    target_day = (seeded_now - timedelta(days=DEMO_RETENTION_DAYS)).date()
    keys_by_age = {
        DEMO_RETENTION_DAYS: tuple(key for _, key in target_day_demo_cases(prefix, target_day)),
        DEMO_RETENTION_DAYS - 1: retained_demo_keys(prefix, target_day),
        0: invalid_demo_keys(prefix, target_day),
    }
    for age_days, keys in keys_by_age.items():
        target = seeded_now - timedelta(days=age_days)
        for key in keys:
            _ = put_test_object(
                client,
                bucket,
                key,
                metadata={
                    "s3-archiver-test-age-days": str(age_days),
                    "s3-archiver-test-last-modified": target.isoformat(),
                },
            )


def target_day_demo_cases(prefix: str, target_day: date) -> tuple[tuple[str, str], ...]:
    day = target_day.isoformat()
    compact = target_day.strftime("%Y%m%d")
    path_day = target_day.strftime("%Y/%m/%d")
    underscore = target_day.strftime("%Y_%m_%d")
    return (
        (f"{prefix}/fae", f"{prefix}/fae/{path_day}/07/{day}T07-00-00Z.xml"),
        (
            f"{prefix}/harmonie",
            f"{prefix}/harmonie/HARMONIE_DINI_SF_{day}T000000Z_{day}T000000Z.bz2",
        ),
        (f"{prefix}/metar", f"{prefix}/metar/{day}/METAR_{compact}120000Z.json"),
        (f"{prefix}/radar", f"{prefix}/radar/{path_day}/radar_{compact}-130000.bin"),
        (f"{prefix}/satellite/flat", f"{prefix}/satellite/flat/sat_{day}T14:30:00Z.png"),
        (f"{prefix}/observations", f"{prefix}/observations/obs_{underscore}_153000.txt"),
        (f"{prefix}/models", f"{prefix}/models/{path_day}/model_{day}T16:45:00+00:00.grib"),
        (f"{prefix}/lightning", f"{prefix}/lightning/{day}T17-00-00Z/lightning-latest.csv"),
        (f"{prefix}/ocean", f"{prefix}/ocean/{path_day}/wave_{day}T18:15:00+0100.nc"),
        (f"{prefix}/climate", f"{prefix}/climate/{compact}/climate-summary.txt"),
        ("C:/compose-demo/unsafe-drive", f"C:/compose-demo/unsafe-drive/{day}T19-00-00Z.txt"),
        (
            f"s3-archiver-safe/{prefix}/reserved",
            f"s3-archiver-safe/{prefix}/reserved/{day}T20-00-00Z.txt",
        ),
    )


def retained_demo_keys(prefix: str, target_day: date) -> tuple[str, str]:
    previous_day = target_day - timedelta(days=1)
    next_day = target_day + timedelta(days=1)
    return (
        f"{prefix}/fae/{next_day:%Y/%m/%d}/00/{next_day.isoformat()}T00-00-00Z.xml",
        f"{prefix}/harmonie/HARMONIE_DINI_SF_{previous_day.isoformat()}T230000Z.bz2",
    )


def invalid_demo_keys(prefix: str, target_day: date) -> tuple[str, str]:
    return (
        f"{prefix}/invalid/no-timestamp-latest.txt",
        f"{prefix}/invalid/{target_day.isoformat()}T99-00-00Z.txt",
    )


def expected_archive_members(prefix: str, target_day: date) -> dict[str, str]:
    return {
        f"{root}/{target_day.isoformat()}.tar.gz": key
        for root, key in target_day_demo_cases(prefix, target_day)
    }


def archive_member_name(key: str) -> str:
    if key.startswith(("C:", "s3-archiver-safe/")):
        return f"s3-archiver-safe/{hashlib.sha256(key.encode()).hexdigest()}"
    return key


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
