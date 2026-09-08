"""Verify the cleanup/late-arrival loss regression against real S3 requests."""

from __future__ import annotations

from pathlib import Path

import pytest
from s3_archiver_core.archive import run_archive
from s3_archiver_core.archive_inspection import inspect_archive
from s3_archiver_core.archive_membership import MEMBERSHIP_METADATA_KEY
from s3_archiver_core.archive_routes import archive_routes_from_settings
from s3_archiver_core.s3 import build_s3_client
from s3_archiver_core.settings import AppSettings
from s3_archiver_localstack_support.harness import LocalstackBucketPair
from s3_archiver_localstack_support.objects import put_test_object

from tests.integration.archive_cli_test_support import (
    FROZEN_ARCHIVE_RUN_STARTED_AT,
    archive_client,
    archive_env,
)


@pytest.mark.integration()
def test_cleaned_archive_survives_late_arrivals_and_can_be_restored(
    tmp_path: Path, localstack_bucket_pair: LocalstackBucketPair
) -> None:
    env = archive_env(tmp_path, localstack_bucket_pair)
    settings = AppSettings.from_env(env)
    client = archive_client(env, "source")
    routes = archive_routes_from_settings(settings, build_s3_client)
    destination = routes[0].destination
    keys = [f"archive/2099-12-30T{hour:02}-00-00Z.grib" for hour in (0, 3)]
    for key in keys:
        _ = put_test_object(client, localstack_bucket_pair.source, key, body=key.encode())
    first = run_archive(
        routes, run_timeout=settings.run_timeout, run_started_at_utc=FROZEN_ARCHIVE_RUN_STARTED_AT
    )
    try:
        assert first.ok
        key = first.manifest.archive_groups[0].destination_archive_key
        before = destination.head_object(key)
        assert before is not None
        assert destination.head_object(before.metadata[MEMBERSHIP_METADATA_KEY]) is not None
        assert inspect_archive(destination, key, output_dir=tmp_path / "restored")[0] == 2
        for original in keys:
            assert (tmp_path / "restored" / original).read_bytes() == original.encode()
        for entry in first.manifest.entries:
            routes[0].source.delete_source_object(entry.key, entry.version_id, if_match=entry.etag)
        for hour in (6, 9, 12):
            _ = put_test_object(
                client, localstack_bucket_pair.source, f"archive/2099-12-30T{hour:02}-00-00Z.grib"
            )
        second = run_archive(
            routes,
            run_timeout=settings.run_timeout,
            run_started_at_utc=FROZEN_ARCHIVE_RUN_STARTED_AT,
        )
        try:
            assert not second.ok
            assert second.cleanup_entries is not None and len(second.cleanup_entries) == 0
            after = destination.head_object(key)
            assert after is not None and after.etag == before.etag
            assert inspect_archive(destination, key)[0] == 2
        finally:
            second.close()
    finally:
        first.close()
