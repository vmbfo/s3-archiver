"""Daily archive verification safety tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest
from s3_archiver_core.archive import (
    ARCHIVE_SHA256_METADATA_KEY,
    MANIFEST_SHA256_METADATA_KEY,
    group_metadata,
    run_archive,
)
from s3_archiver_core.archive_manifest import build_archive_manifest

from tests.unit.archive_workflow_fakes import FakeBucket, archive_routes, daily_run_timeout
from tests.unit.archive_workflow_fakes import listed_object as _listed
from tests.unit.archive_workflow_fakes import object_properties as _properties

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)
IN_PROGRESS_DAY_STARTED = datetime(2026, 4, 27, 18, tzinfo=UTC)
IN_PROGRESS_DAY_KEY = "data/fae/2026/04/27/12/2026-04-27T12-00-00.xml"


@pytest.mark.unit()
def test_existing_archive_requires_archive_hash_before_reuse() -> None:
    listed = _listed("data/fae/2026/04/13/07/2026-04-13T07-00-00.xml", 1, "v1")
    source = FakeBucket("source", (listed,))
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=STARTED,
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
    )
    archive_key = manifest.archive_groups[0].destination_archive_key
    missing_hash = FakeBucket(
        "destination",
        destination={archive_key: _properties(metadata=group_metadata(manifest.archive_groups[0]))},
    )

    result = run_archive(
        archive_routes(source, missing_hash),
        run_timeout=daily_run_timeout(),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )

    assert result.copy.failures == (f"{archive_key}: archive verification failed",)
    assert result.verify.skipped is True


@pytest.mark.unit()
def test_existing_archive_rejects_mismatched_source_identity() -> None:
    listed = _listed("data/fae/2026/04/13/07/2026-04-13T07-00-00.xml", 1, "v1")
    source = FakeBucket("source", (listed,))
    destination_bucket = FakeBucket("destination")
    other_manifest = build_archive_manifest(
        source,
        run_started_at_utc=STARTED,
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
        destination=destination_bucket,
        source_identity=("other", "source"),
    )
    archive_key = other_manifest.archive_groups[0].destination_archive_key
    payload = b"archive"
    existing_metadata = dict(group_metadata(other_manifest.archive_groups[0])) | {
        ARCHIVE_SHA256_METADATA_KEY: hashlib.sha256(payload).hexdigest()
    }
    destination = FakeBucket(
        "destination",
        destination={archive_key: _properties(metadata=existing_metadata)},
        payloads={archive_key: payload},
    )

    result = run_archive(
        archive_routes(source, destination),
        run_timeout=daily_run_timeout(),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )

    assert result.copy.failures == (f"{archive_key}: archive verification failed",)
    assert result.verify.skipped is True


@pytest.mark.unit()
def test_mismatched_existing_archive_fails_only_that_group() -> None:
    good = _listed("data/fae/2026/04/13/07/2026-04-13T07-00-00.xml", 1, "v-good")
    skipped = _listed("data/harmonie/2026-04-13T07-00-00.xml", 1, "v-skip")
    source = FakeBucket("source", (good, skipped))
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=STARTED,
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
    )
    good_group = next(
        group for group in manifest.archive_groups if group.archive_root == "data/fae"
    )
    skipped_group = next(
        group for group in manifest.archive_groups if group.archive_root == "data/harmonie"
    )
    payload = b"verified"
    good_metadata = dict(group_metadata(good_group)) | {
        ARCHIVE_SHA256_METADATA_KEY: hashlib.sha256(payload).hexdigest()
    }
    destination = FakeBucket(
        "destination",
        destination={
            good_group.destination_archive_key: _properties(metadata=good_metadata),
            skipped_group.destination_archive_key: _properties(
                metadata={MANIFEST_SHA256_METADATA_KEY: "different"}
            ),
        },
        payloads={good_group.destination_archive_key: payload},
    )

    result = run_archive(
        archive_routes(source, destination),
        run_timeout=daily_run_timeout(),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )

    assert result.ok is False
    assert result.copy.failures == (
        f"{skipped_group.destination_archive_key}: archive verification failed",
    )


@pytest.mark.unit()
def test_in_progress_day_overwrites_existing_archive_on_metadata_mismatch() -> None:
    """Today's archive grows during the day; overwrite stale destination instead of failing."""

    listed = _listed(IN_PROGRESS_DAY_KEY, 1, "v1")
    source = FakeBucket("source", (listed,))
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=IN_PROGRESS_DAY_STARTED,
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
    )
    group = manifest.archive_groups[0]
    archive_key = group.destination_archive_key
    assert group.target_day == IN_PROGRESS_DAY_STARTED.date()
    stale_metadata = dict(group_metadata(group)) | {
        MANIFEST_SHA256_METADATA_KEY: "stale-manifest-hash",
        ARCHIVE_SHA256_METADATA_KEY: hashlib.sha256(b"stale").hexdigest(),
    }
    destination = FakeBucket(
        "destination",
        destination={archive_key: _properties(metadata=stale_metadata)},
        payloads={archive_key: b"stale"},
    )

    result = run_archive(
        archive_routes(source, destination),
        run_timeout=daily_run_timeout(),
        run_started_at_utc=IN_PROGRESS_DAY_STARTED,
        clock=lambda: IN_PROGRESS_DAY_STARTED,
    )

    assert result.copy.failures == ()
    assert result.ok is True
    assert destination.uploaded == [archive_key]


@pytest.mark.unit()
def test_in_progress_day_skips_when_existing_archive_metadata_matches() -> None:
    """A re-run with identical source still no-ops when today's archive metadata matches."""

    listed = _listed(IN_PROGRESS_DAY_KEY, 1, "v1")
    source = FakeBucket("source", (listed,))
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=IN_PROGRESS_DAY_STARTED,
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
    )
    group = manifest.archive_groups[0]
    archive_key = group.destination_archive_key
    matching_metadata = dict(group_metadata(group)) | {
        ARCHIVE_SHA256_METADATA_KEY: hashlib.sha256(b"matching").hexdigest(),
    }
    destination = FakeBucket(
        "destination",
        destination={archive_key: _properties(metadata=matching_metadata)},
        payloads={archive_key: b"matching"},
    )

    result = run_archive(
        archive_routes(source, destination),
        run_timeout=daily_run_timeout(),
        run_started_at_utc=IN_PROGRESS_DAY_STARTED,
        clock=lambda: IN_PROGRESS_DAY_STARTED,
    )

    assert result.copy.failures == ()
    assert result.ok is True
    assert destination.uploaded == []
