"""Archive command integration tests against isolated LocalStack buckets."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import pytest

from tests.integration.archive_cli_test_support import archive_client as _client
from tests.integration.archive_cli_test_support import archive_env as _archive_env
from tests.integration.archive_cli_test_support import run_archive_command as _run_archive
from tests.integration.localstack_harness import LocalstackBucketPair
from tests.integration.localstack_object_helpers import (
    listed_keys,
    put_test_object,
    seed_timestamped_objects,
)
from tests.integration.test_localstack_timestamp_seed import SEED_NOW


class ArchivePayload(TypedDict):
    status: str
    source_bucket: str
    destination_bucket: str
    manifest: dict[str, object]
    phases: dict[str, dict[str, object]]


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
    for key in source_keys:
        _ = put_test_object(source_client, localstack_bucket_pair.source, key)

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
    assert listed_keys(destination_client, localstack_bucket_pair.destination) == source_keys
    expected_source_keys = set() if cleanup_value == "true" else source_keys
    assert listed_keys(source_client, localstack_bucket_pair.source) == expected_source_keys


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
    for key in {"include/a.txt", "include/nested/b.txt", "exclude/c.txt"}:
        _ = put_test_object(source_client, localstack_bucket_pair.source, key)

    payload = _run_archive(monkeypatch, env)

    assert payload["status"] == "ok"
    assert payload["manifest"]["object_count"] == 2
    assert listed_keys(destination_client, localstack_bucket_pair.destination) == {
        "include/a.txt",
        "include/nested/b.txt",
    }
    assert listed_keys(source_client, localstack_bucket_pair.source) == {"exclude/c.txt"}


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
    for key in {"allowed/a.txt", "blocked/b.txt", "blocked/nested/c.txt"}:
        _ = put_test_object(source_client, localstack_bucket_pair.source, key)

    payload = _run_archive(monkeypatch, env)

    assert payload["status"] == "ok"
    assert payload["manifest"]["object_count"] == 1
    assert listed_keys(destination_client, localstack_bucket_pair.destination) == {"allowed/a.txt"}
    assert listed_keys(source_client, localstack_bucket_pair.source) == {
        "blocked/b.txt",
        "blocked/nested/c.txt",
    }


@pytest.mark.integration()
@pytest.mark.parametrize(
    ("retention_days", "expected_days"),
    [(1, {59, 60, 61}), (60, {61})],
)
def test_archive_command_retention_matrix_uses_seeded_last_modified_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
    retention_days: int,
    expected_days: set[int],
) -> None:
    prefix = "retention-boundary"
    source_seed_env = _archive_env(tmp_path, localstack_bucket_pair)
    seed_timestamped_objects(
        _client(source_seed_env, "source"),
        localstack_bucket_pair.source,
        prefix=prefix,
        days=(59, 60, 61),
        seed_now=SEED_NOW,
    )
    env = _archive_env(tmp_path, localstack_bucket_pair)
    env["ARCHIVER_RETENTION_DAYS"] = str(retention_days)
    source_client = _client(env, "source")
    destination_client = _client(env, "destination")

    payload = _run_archive(monkeypatch, env)

    assert payload["status"] == "ok"
    assert payload["manifest"]["object_count"] == len(expected_days)
    assert listed_keys(destination_client, localstack_bucket_pair.destination) == {
        f"{prefix}/age-{day}-days.txt" for day in expected_days
    }
    assert listed_keys(source_client, localstack_bucket_pair.source) == {
        f"{prefix}/age-{day}-days.txt" for day in {59, 60, 61}
    }


def _phase_statuses(payload: ArchivePayload) -> dict[str, object]:
    return {
        name: phase["status"]
        for name, phase in payload["phases"].items()
        if name in {"list", "copy", "verify", "cleanup"}
    }
