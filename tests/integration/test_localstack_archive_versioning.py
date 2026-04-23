"""Versioning-focused archive command integration tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from mypy_boto3_s3.type_defs import VersioningConfigurationTypeDef

from tests.integration.archive_cli_test_support import archive_client as _client
from tests.integration.archive_cli_test_support import archive_env as _archive_env
from tests.integration.archive_cli_test_support import run_archive_command as _run_archive
from tests.integration.localstack_harness import LocalstackBucketPair
from tests.integration.localstack_object_helpers import (
    listed_key_versions,
    listed_keys,
    put_test_object,
    read_object_text,
)


@pytest.mark.integration()
def test_archive_command_cleans_up_exact_latest_version_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    env = _archive_env(tmp_path, localstack_bucket_pair)
    env["ARCHIVER_ENABLE_CLEANUP"] = "true"
    source_client = _client(env, "source")
    destination_client = _client(env, "destination")
    _ = source_client.put_bucket_versioning(
        Bucket=localstack_bucket_pair.source,
        VersioningConfiguration={"Status": "Enabled"},
    )
    key = "versioned/history.txt"
    first = put_test_object(source_client, localstack_bucket_pair.source, key, body=b"first\n")
    second = put_test_object(source_client, localstack_bucket_pair.source, key, body=b"second\n")

    payload = _run_archive(monkeypatch, env)

    assert payload["status"] == "ok"
    assert payload["manifest"]["object_count"] == 1
    assert (
        read_object_text(destination_client, localstack_bucket_pair.destination, key) == "second\n"
    )
    assert read_object_text(source_client, localstack_bucket_pair.source, key) == "first\n"
    versions = listed_key_versions(source_client, localstack_bucket_pair.source, key)
    assert (key, str(first["VersionId"]), True) in versions
    assert all(version_id != str(second["VersionId"]) for _, version_id, _ in versions)


@pytest.mark.integration()
def test_archive_command_rerun_recovers_archived_source_version_for_exact_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    key = "versioned/rerun.txt"
    first_env = _archive_env(tmp_path, localstack_bucket_pair)
    first_env["ARCHIVER_ENABLE_CLEANUP"] = "false"
    source_client = _client(first_env, "source")
    destination_client = _client(first_env, "destination")
    _ = source_client.put_bucket_versioning(
        Bucket=localstack_bucket_pair.source,
        VersioningConfiguration={"Status": "Enabled"},
    )
    archived = put_test_object(
        source_client, localstack_bucket_pair.source, key, body=b"archived-version\n"
    )

    first_payload = _run_archive(monkeypatch, first_env)

    assert first_payload["status"] == "ok"
    assert read_object_text(destination_client, localstack_bucket_pair.destination, key) == (
        "archived-version\n"
    )
    live = put_test_object(source_client, localstack_bucket_pair.source, key, body=b"live-now\n")
    rerun_env = dict(first_env)
    rerun_env["ARCHIVER_ENABLE_CLEANUP"] = "true"

    rerun_payload = _run_archive(monkeypatch, rerun_env)

    assert rerun_payload["status"] == "ok"
    assert rerun_payload["manifest"]["object_count"] == 1
    destination_metadata = cast(
        dict[str, str],
        destination_client.head_object(Bucket=localstack_bucket_pair.destination, Key=key)[
            "Metadata"
        ],
    )
    fingerprint = json.loads(destination_metadata["s3-archiver-source-fingerprint"])
    assert fingerprint["source_version_id"] == str(archived["VersionId"])
    assert read_object_text(destination_client, localstack_bucket_pair.destination, key) == (
        "archived-version\n"
    )
    assert read_object_text(source_client, localstack_bucket_pair.source, key) == "live-now\n"
    versions = listed_key_versions(source_client, localstack_bucket_pair.source, key)
    assert (key, str(live["VersionId"]), True) in versions
    assert all(version_id != str(archived["VersionId"]) for _, version_id, _ in versions)


@pytest.mark.integration()
def test_archive_command_cleans_up_null_version_when_localstack_supports_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    env = _archive_env(tmp_path, localstack_bucket_pair)
    env["ARCHIVER_ENABLE_CLEANUP"] = "true"
    source_client = _client(env, "source")
    destination_client = _client(env, "destination")
    key = "versioned/null.txt"
    enabled: VersioningConfigurationTypeDef = {"Status": "Enabled"}
    suspended: VersioningConfigurationTypeDef = {"Status": "Suspended"}
    _ = source_client.put_bucket_versioning(
        Bucket=localstack_bucket_pair.source,
        VersioningConfiguration=enabled,
    )
    base = put_test_object(source_client, localstack_bucket_pair.source, key, body=b"base\n")
    _ = source_client.put_bucket_versioning(
        Bucket=localstack_bucket_pair.source,
        VersioningConfiguration=suspended,
    )
    current = put_test_object(
        source_client, localstack_bucket_pair.source, key, body=b"null-current\n"
    )
    if current.get("VersionId") != "null":
        pytest.skip("LocalStack did not expose suspended null versions for cleanup assertions")

    payload = _run_archive(monkeypatch, env)

    assert payload["status"] == "ok"
    assert (
        read_object_text(destination_client, localstack_bucket_pair.destination, key)
        == "null-current\n"
    )
    assert key not in listed_keys(source_client, localstack_bucket_pair.source)
    assert any(
        version_id == str(base["VersionId"])
        for _, version_id, _ in listed_key_versions(
            source_client, localstack_bucket_pair.source, key
        )
    )
