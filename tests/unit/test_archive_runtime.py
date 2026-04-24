"""Runtime-oriented archive workflow unit tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import override

import pytest
from s3_archiver_core.archive import run_archive
from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_transfer import FINGERPRINT_METADATA_KEY, archive_metadata
from s3_archiver_core.s3 import VersioningState

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed
from tests.unit.archive_workflow_fakes import object_properties as _properties

STARTED = datetime(2024, 4, 20, tzinfo=UTC)


def _clock() -> datetime:
    return STARTED


@pytest.mark.unit()
def test_rerun_rejects_unversioned_destination_when_source_last_modified_changed() -> None:
    archived_listed = replace(
        _listed("old.txt", 90, None),
        properties=_properties(
            last_modified=datetime(2024, 1, 21, tzinfo=UTC),
        ),
    )
    stored_entry = ManifestEntry(
        "source",
        "old.txt",
        10,
        archived_listed.last_modified,
        '"etag"',
        None,
        archived_listed,
    )
    current_listed = replace(
        archived_listed,
        last_modified=archived_listed.last_modified + timedelta(seconds=1),
        properties=_properties(
            last_modified=archived_listed.last_modified + timedelta(seconds=1),
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

    assert result.copy.failures == ("old.txt: source fingerprint mismatch",)


@pytest.mark.unit()
def test_run_archive_orders_phases_and_gates_cleanup() -> None:
    source = FakeBucket("source", (_listed("old.txt", 90, "v1"),))
    destination = FakeBucket("destination")
    decisions: list[tuple[str, str]] = []
    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=False, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
        debug_logger=lambda entry, strategy: decisions.append((entry.key, strategy)),
    )
    assert result.ok is True
    assert destination.copied == ["old.txt"]
    assert source.deleted == []
    assert decisions == [("old.txt", "simple_native_copy")]
    assert result.cleanup.skipped is True
    cleanup_result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert cleanup_result.ok is True
    assert source.deleted == [("old.txt", "v1")]
    assert cleanup_result.cleanup.skipped is False


@pytest.mark.unit()
def test_copy_or_verify_failure_blocks_later_phases() -> None:
    source = FakeBucket("source", (_listed("old.txt", 90),))
    failing_destination = FakeBucket("destination")
    failing_destination.fail_copy = True

    copy_failed = run_archive(
        source,
        failing_destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=2),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert copy_failed.copy.ok is False
    assert copy_failed.verify.skipped is True
    assert source.deleted == []

    bad_destination = FakeBucket("destination", destination={"old.txt": _properties(size=10)})
    verify_failed = run_archive(
        source,
        bad_destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert verify_failed.copy.failures == ("old.txt: source fingerprint mismatch",)
    assert verify_failed.verify.skipped is True
    assert source.deleted == []

    listed = _listed("old.txt", 90)
    entry = ManifestEntry("source", "old.txt", 10, listed.last_modified, '"etag"', "v1", listed)
    metadata = archive_metadata(entry)
    wrong_payload_destination = FakeBucket(
        "destination",
        destination={"old.txt": replace(entry.object.properties, metadata=metadata)},
        payloads={"old.txt": b"different"},
    )
    content_failed = run_archive(
        source,
        wrong_payload_destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert content_failed.copy.failures == ("old.txt: content mismatch",)
    assert source.deleted == []


@pytest.mark.unit()
def test_concurrent_worker_failures_keep_object_key_for_reporting() -> None:
    listed = replace(
        _listed("old.txt", 90),
        properties=_properties(metadata={FINGERPRINT_METADATA_KEY: "source-owned"}),
    )
    source = FakeBucket("source", (listed,))
    destination = FakeBucket("destination")

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=2),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert result.copy.failures == (
        f"old.txt: source metadata uses reserved key {FINGERPRINT_METADATA_KEY}",
    )
    assert result.verify.skipped is True
    assert source.deleted == []


@pytest.mark.unit()
def test_run_archive_timeout_blocks_later_phases() -> None:
    started = datetime(2024, 4, 20, tzinfo=UTC)
    source = FakeBucket("source", (_listed("old.txt", 90),))
    destination = FakeBucket("destination")

    timed_out = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=started,
        clock=lambda: started + timedelta(days=8),
    )

    assert timed_out.copy.failures == ("archive run timed out",)
    assert timed_out.verify.skipped is True
    assert source.deleted == []


@pytest.mark.unit()
def test_list_failure_blocks_archive_phases() -> None:
    class BrokenListBucket(FakeBucket):
        @override
        def versioning_state(self) -> VersioningState:
            raise RuntimeError("source.txt: list failed")

    result = run_archive(
        BrokenListBucket("source"),
        FakeBucket("destination"),
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert result.list.failures == ("source.txt: list failed",)
    assert result.copy.skipped is True
    assert result.verify.skipped is True
    assert result.cleanup.skipped is True


@pytest.mark.unit()
def test_key_only_cleanup_rechecks_source_before_delete() -> None:
    source = FakeBucket(
        "source",
        (_listed("old.txt", 90, None),),
        destination={"old.txt": _properties(size=11)},
    )
    destination = FakeBucket("destination")

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert result.cleanup.failures == ("old.txt: source changed before cleanup",)
    assert source.deleted == []
