"""Unit tests for archive workflow primitives."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from s3_archiver_core.archive import run_archive
from s3_archiver_core.archive_manifest import (
    ManifestEntry,
    SourcePathFilter,
    build_archive_manifest,
)
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_transfer import (
    FINGERPRINT_METADATA_KEY,
    archive_metadata,
    select_transfer_strategy,
    verify_destination,
    verify_destination_checksum,
    verify_destination_content,
    verify_source_unchanged,
)
from s3_archiver_core.s3 import S3TransferCapabilities

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed
from tests.unit.archive_workflow_fakes import object_properties as _properties

STARTED = datetime(2024, 4, 20, tzinfo=UTC)


def _clock() -> datetime:
    return STARTED


@pytest.mark.unit()
def test_manifest_uses_frozen_cutoff_filters_and_preserves_versions() -> None:
    source = FakeBucket(
        "source",
        (
            _listed("keep/old.txt", 61, "v-old"),
            _listed("keep/boundary.txt", 60, "v-boundary"),
            _listed("skip/old.txt", 90, "v-skip"),
        ),
    )

    manifest = build_archive_manifest(
        source,
        run_started_at_utc=STARTED,
        retention_days=60,
        versioning_state="Enabled",
        source_filter=SourcePathFilter("whitelist", ("keep/",)),
    )

    assert manifest.retention_cutoff_utc == datetime(2024, 2, 20, tzinfo=UTC)
    assert [(entry.key, entry.version_id) for entry in manifest.entries] == [
        ("keep/old.txt", "v-old")
    ]


@pytest.mark.unit()
def test_transfer_strategy_selection_and_fingerprint_verification() -> None:
    listed = _listed("key.txt", 70)
    entry = ManifestEntry("source", "key.txt", 10, listed.last_modified, '"etag"', "v1", listed)
    metadata = archive_metadata(entry)
    destination = replace(entry.object.properties, metadata=metadata)

    assert verify_destination(entry, destination).ok is True
    assert verify_destination_checksum(entry.object.properties, destination) is None
    assert verify_destination(entry, replace(destination, size=11)).detail == "size mismatch"
    assert verify_destination_content("digest", "digest").ok is True
    assert verify_destination_content(None, "digest").detail == "source missing during verification"
    assert verify_destination_content("digest", None).detail == "destination missing"
    assert (
        select_transfer_strategy(10, S3TransferCapabilities(), simple_copy_limit_bytes=10)
        == "simple_native_copy"
    )
    assert (
        select_transfer_strategy(11, S3TransferCapabilities(), simple_copy_limit_bytes=10)
        == "multipart_native_copy"
    )
    assert (
        select_transfer_strategy(
            11,
            S3TransferCapabilities(native_copy=False),
            simple_copy_limit_bytes=10,
        )
        == "multipart_streaming"
    )
    assert (
        select_transfer_strategy(
            51,
            S3TransferCapabilities(native_copy=False, streaming_upload=False),
            streaming_limit_bytes=50,
        )
        == "temp_file_backed"
    )
    reserved = replace(listed, properties=_properties(metadata={FINGERPRINT_METADATA_KEY: "user"}))
    reserved_entry = ManifestEntry(
        "source", "key.txt", 10, listed.last_modified, None, "v1", reserved
    )
    with pytest.raises(ValueError, match="reserved key"):
        _ = archive_metadata(reserved_entry)


@pytest.mark.unit()
def test_key_only_cleanup_verification_rejects_etag_changes() -> None:
    listed = _listed("key.txt", 70, None)
    entry = ManifestEntry("source", "key.txt", 10, listed.last_modified, '"etag"', None, listed)

    result = verify_source_unchanged(
        entry,
        replace(entry.object.properties, etag='"changed"'),
    )

    assert result.ok is False
    assert result.detail == "source changed before cleanup"


@pytest.mark.unit()
def test_key_only_cleanup_verification_rejects_last_modified_changes() -> None:
    listed = _listed("key.txt", 70, None)
    entry = ManifestEntry("source", "key.txt", 10, listed.last_modified, '"etag"', None, listed)

    result = verify_source_unchanged(
        entry,
        replace(
            entry.object.properties,
            last_modified=entry.last_modified + timedelta(seconds=1),
        ),
    )

    assert result.ok is False
    assert result.detail == "source changed before cleanup"


@pytest.mark.unit()
def test_run_archive_prefers_object_checksums_before_streaming_hash() -> None:
    checksum = {"sha256": "digest"}
    listed = replace(
        _listed("old.txt", 90),
        properties=_properties(
            last_modified=datetime(2024, 1, 21, tzinfo=UTC),
            checksums=checksum,
            checksum_type="FULL_OBJECT",
        ),
    )
    source = FakeBucket("source", (listed,))
    destination = FakeBucket(
        "destination",
        destination={
            "old.txt": replace(
                listed.properties,
                metadata=archive_metadata(
                    ManifestEntry(
                        "source",
                        "old.txt",
                        10,
                        listed.last_modified,
                        '"etag"',
                        "v1",
                        listed,
                    )
                ),
            )
        },
    )

    def unexpected_hash(*_args: object, **_kwargs: object) -> str | None:
        raise AssertionError("streaming hash fallback should not run when checksums match")

    source.content_sha256 = unexpected_hash  # type: ignore[method-assign]
    destination.content_sha256 = unexpected_hash  # type: ignore[method-assign]

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=False, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert result.ok is True


@pytest.mark.unit()
def test_run_archive_falls_back_to_streaming_hash_for_composite_checksums() -> None:
    checksum = {"sha256": "digest"}
    listed = replace(
        _listed("old.txt", 90),
        properties=_properties(
            last_modified=datetime(2024, 1, 21, tzinfo=UTC),
            checksums=checksum,
            checksum_type="COMPOSITE",
        ),
    )
    entry = ManifestEntry("source", "old.txt", 10, listed.last_modified, '"etag"', "v1", listed)
    source = FakeBucket("source", (listed,))
    destination = FakeBucket(
        "destination",
        destination={
            "old.txt": replace(
                listed.properties,
                metadata=archive_metadata(entry),
                checksum_type="COMPOSITE",
            )
        },
    )

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=False, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert result.ok is True
    assert source.content_sha256_calls == [("old.txt", "v1"), ("old.txt", "v1")]
    assert destination.content_sha256_calls == [("old.txt", None), ("old.txt", None)]


@pytest.mark.unit()
def test_verify_destination_checksum_reports_checksum_mismatch() -> None:
    source = _properties(checksums={"sha256": "expected"}, checksum_type="FULL_OBJECT")
    destination = _properties(checksums={"sha256": "other"}, checksum_type="FULL_OBJECT")

    result = verify_destination_checksum(source, destination)

    assert result is not None
    assert result.ok is False


@pytest.mark.unit()
def test_rerun_accepts_persisted_checksums_when_current_source_no_longer_exposes_them() -> None:
    checksummed_listed = replace(
        _listed("old.txt", 90),
        properties=_properties(
            last_modified=datetime(2024, 1, 21, tzinfo=UTC),
            checksums={"sha256": "digest"},
            checksum_type="FULL_OBJECT",
        ),
    )
    stored_entry = ManifestEntry(
        "source",
        "old.txt",
        10,
        checksummed_listed.last_modified,
        '"etag"',
        "v1",
        checksummed_listed,
    )
    current_listed = replace(
        checksummed_listed,
        properties=_properties(
            last_modified=datetime(2024, 1, 21, tzinfo=UTC),
        ),
    )
    source = FakeBucket("source", (current_listed,))
    destination = FakeBucket(
        "destination",
        destination={
            "old.txt": replace(current_listed.properties, metadata=archive_metadata(stored_entry))
        },
    )

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=False, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert result.ok is True
