"""Versioning-focused archive command integration tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TypedDict, cast

import pytest
from mypy_boto3_s3.type_defs import VersioningConfigurationTypeDef
from s3_archiver_core.archive_s3 import S3ArchiveBucket

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


class SourceFingerprintPayload(TypedDict):
    source_version_id: str | None


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
    fingerprint = cast(
        SourceFingerprintPayload,
        json.loads(destination_metadata["s3-archiver-source-fingerprint"]),
    )
    assert fingerprint["source_version_id"] == str(archived["VersionId"])
    assert read_object_text(destination_client, localstack_bucket_pair.destination, key) == (
        "archived-version\n"
    )
    assert read_object_text(source_client, localstack_bucket_pair.source, key) == "live-now\n"
    versions = listed_key_versions(source_client, localstack_bucket_pair.source, key)
    assert (key, str(live["VersionId"]), True) in versions
    assert all(version_id != str(archived["VersionId"]) for _, version_id, _ in versions)


@pytest.mark.integration()
def test_archive_command_cleans_up_pre_versioning_null_version(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    env = _archive_env(tmp_path, localstack_bucket_pair)
    env["ARCHIVER_ENABLE_CLEANUP"] = "true"
    source_client = _client(env, "source")
    destination_client = _client(env, "destination")
    key = "versioned/null.txt"
    _ = put_test_object(source_client, localstack_bucket_pair.source, key, body=b"null-current\n")
    enabled: VersioningConfigurationTypeDef = {"Status": "Enabled"}
    _ = source_client.put_bucket_versioning(
        Bucket=localstack_bucket_pair.source,
        VersioningConfiguration=enabled,
    )
    versions_before = listed_key_versions(source_client, localstack_bucket_pair.source, key)
    assert versions_before == [(key, "null", True)], (
        "LocalStack must expose pre-versioning current objects as VersionId='null' "
        "for the null-version cleanup contract"
    )

    payload = _run_archive(monkeypatch, env)

    assert payload["status"] == "ok"
    assert (
        read_object_text(destination_client, localstack_bucket_pair.destination, key)
        == "null-current\n"
    )
    destination_metadata = cast(
        dict[str, str],
        destination_client.head_object(Bucket=localstack_bucket_pair.destination, Key=key)[
            "Metadata"
        ],
    )
    fingerprint = cast(
        SourceFingerprintPayload,
        json.loads(destination_metadata["s3-archiver-source-fingerprint"]),
    )
    assert fingerprint["source_version_id"] is None
    assert key not in listed_keys(source_client, localstack_bucket_pair.source)
    versions = listed_key_versions(source_client, localstack_bucket_pair.source, key)
    assert versions == [(key, "null", False)]


@pytest.mark.integration()
def test_versioned_listing_paginates_across_pages_and_filters_delete_markers(
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    env = _archive_env(tmp_path, localstack_bucket_pair)
    source_client = _client(env, "source")
    _ = source_client.put_bucket_versioning(
        Bucket=localstack_bucket_pair.source,
        VersioningConfiguration={"Status": "Enabled"},
    )
    early_deleted_keys = {f"aaa-deleted-{index:04d}.txt" for index in range(2)}
    live_keys = {f"mmm-live-{index:04d}.txt" for index in range(998)}
    late_deleted_keys = {f"zzz-deleted-{index:04d}.txt" for index in range(2)}
    for key in sorted(early_deleted_keys):
        _ = put_test_object(source_client, localstack_bucket_pair.source, key)
        _ = source_client.delete_object(Bucket=localstack_bucket_pair.source, Key=key)
    for key in sorted(live_keys):
        _ = put_test_object(source_client, localstack_bucket_pair.source, key)
    for key in sorted(late_deleted_keys):
        _ = put_test_object(source_client, localstack_bucket_pair.source, key)
        _ = source_client.delete_object(Bucket=localstack_bucket_pair.source, Key=key)

    first_page = source_client.list_object_versions(
        Bucket=localstack_bucket_pair.source,
        MaxKeys=1000,
    )
    second_page = source_client.list_object_versions(
        Bucket=localstack_bucket_pair.source,
        MaxKeys=1000,
        KeyMarker=str(first_page["NextKeyMarker"]),
        VersionIdMarker=str(first_page["NextVersionIdMarker"]),
    )
    listed = list(
        S3ArchiveBucket(source_client, localstack_bucket_pair.source).list_source_objects("Enabled")
    )

    assert first_page["IsTruncated"] is True
    assert _delete_marker_keys(first_page) == early_deleted_keys
    assert _delete_marker_keys(second_page) == late_deleted_keys
    assert {entry.key for entry in listed} == live_keys
    assert len(listed) == len(live_keys)
    assert all(entry.version_id is not None for entry in listed)


def _delete_marker_keys(page: Mapping[str, object]) -> set[str]:
    delete_markers = page.get("DeleteMarkers")
    if not isinstance(delete_markers, list):
        return set()
    keys: set[str] = set()
    for raw_marker in cast(list[object], delete_markers):
        if not isinstance(raw_marker, dict):
            continue
        marker = cast(dict[str, object], raw_marker)
        if marker.get("Key") is not None:
            keys.add(str(marker["Key"]))
    return keys
