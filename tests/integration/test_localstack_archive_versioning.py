"""Versioning-focused archive command integration tests."""

from __future__ import annotations

from pathlib import Path

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
