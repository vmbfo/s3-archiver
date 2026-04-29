"""Compose e2e coverage for the cleanup-performing visual demo command."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from s3_archiver_core.archive_tar import ORIGINAL_KEY_PAX_HEADER

from tests.e2e.test_compose_visual_demo import (
    COMPOSE_RETRYABLE_MESSAGES,
    COMPOSE_RETRYABLE_RETURNCODES,
    DEMO_RETENTION_DAYS,
    DEMO_SEEDED_OBJECT_COUNT,
    REPO_ROOT,
    VISUAL_DEMO_RETRIES,
    VISUAL_DEMO_RETRY_DELAY_SECONDS,
    archive_member_name,
    cleanup_statuses,
    demo_client,
    demo_payload,
    expected_archive_members,
    invalid_demo_keys,
    retained_demo_keys,
    run_demo_compose,
    seed_daily_demo_objects,
    target_day_demo_cases,
    write_demo_env_file,
)
from tests.e2e.visual_demo_summary import print_verified_summary
from tests.e2e.visual_demo_terminal import run_visual_demo as render_visual_demo
from tests.integration.localstack_harness import LocalstackBucketPair
from tests.integration.localstack_object_helpers import (
    listed_keys,
    read_tar_gz_member_pax_headers,
    read_tar_gz_members_text,
)


@pytest.mark.e2e()
def test_compose_cleanup_demo_streams_real_cleanup_and_finishes_with_json_summary(
    tmp_path: Path,
    compose_env: dict[str, str],
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    bucket_pair = localstack_bucket_pair
    source_client = demo_client(tmp_path, bucket_pair, "source")
    destination_client = demo_client(tmp_path, bucket_pair, "destination")
    source_prefix = "compose-cleanup-demo"
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
    env_file = write_demo_env_file(tmp_path, bucket_pair, cleanup_enabled=True)
    run_env = dict(compose_env)
    run_env["APP_ENV_FILE"] = str(env_file)

    result = _run_visual_cleanup_demo(run_env)
    payload = demo_payload(result.stdout)

    assert "== S3 Archiver Cleanup Visual Demo ==" in result.stdout
    assert "== Before archive ==" in result.stdout
    assert "== Archive Candidates ==" in result.stdout
    assert "== After cleanup ==" in result.stdout
    assert "== Cleanup Preview ==" not in result.stdout
    assert f"cleanup deleted source object count: {len(archived_keys)}" in result.stdout
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
    assert payload["status"] == "ok"
    assert payload["cleanup_mode"] == "cleanup"
    assert payload["cleanup_performed"] is True
    assert payload["cleanup_preview"] is None
    assert payload["cleanup_deleted_source_object_count"] == len(archived_keys)
    archive_manifest = cast(dict[str, object], payload["archive_manifest"])
    archive_result = cast(dict[str, object], payload["archive_result"])
    assert archive_manifest["object_count"] == len(archived_keys)
    assert archive_manifest["destination_archive_keys"] == sorted(archive_keys)
    assert archive_manifest["archive_count"] == len(archive_keys)
    assert archive_manifest["skipped_object_count"] == len(retained_keys | invalid_keys)
    assert cleanup_statuses(archive_result) == ["ok"] * len(archive_keys)
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
    assert listed_keys(source_client, bucket_pair.source) == retained_keys | invalid_keys
    print_verified_summary(
        payload,
        total_count=len(source_keys),
        copied_count=len(archived_keys),
        remaining_source_count=len(retained_keys | invalid_keys),
    )


def _run_visual_cleanup_demo(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return render_visual_demo(
        env,
        repo_root=REPO_ROOT,
        cli_command="demo-cleanup",
        compose_runner=run_demo_compose,
        retryable_messages=COMPOSE_RETRYABLE_MESSAGES,
        retryable_returncodes=COMPOSE_RETRYABLE_RETURNCODES,
        retries=VISUAL_DEMO_RETRIES,
        retry_delay_seconds=VISUAL_DEMO_RETRY_DELAY_SECONDS,
        retention_days=DEMO_RETENTION_DAYS,
        seeded_count=DEMO_SEEDED_OBJECT_COUNT,
    )
