"""Unit tests for archive workflow edge cases."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import override

import pytest
from s3_archiver_core.archive import run_archive
from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_transfer import (
    FINGERPRINT_METADATA_KEY,
    TransferStrategy,
    archive_metadata,
    fingerprint_from_metadata,
    verify_destination,
    verify_source_unchanged,
)
from s3_archiver_core.s3 import S3ObjectProperties, VersioningState

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2024, 4, 20, tzinfo=UTC)


class FakeRunLock:
    """Archive run lock test double."""

    def __init__(self, *, acquired: bool = True) -> None:
        self.acquired: bool = acquired
        self.released: list[str] = []

    def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: timedelta) -> bool:
        _ = (run_id, run_started_at_utc, timeout)
        return self.acquired

    def release(self, *, run_id: str) -> None:
        self.released.append(run_id)


class SequenceClock:
    """Return the start time for a while, then an expired time."""

    def __init__(self, expire_after_calls: int) -> None:
        self.expire_after_calls: int = expire_after_calls
        self.calls: int = 0

    def __call__(self) -> datetime:
        self.calls += 1
        if self.calls > self.expire_after_calls:
            return STARTED + timedelta(days=8)
        return STARTED


def _entry(key: str = "old.txt") -> ManifestEntry:
    listed = _listed(key, 90, None)
    return ManifestEntry("source", key, 10, listed.last_modified, '"etag"', None, listed)


@pytest.mark.unit()
def test_run_archive_rejects_held_lock_and_releases_acquired_lock() -> None:
    with pytest.raises(RuntimeError, match="lock is already held"):
        _ = run_archive(
            FakeBucket("source"),
            FakeBucket("destination"),
            ArchiveOptions(retention_days=60),
            run_started_at_utc=STARTED,
            run_lock=FakeRunLock(acquired=False),
            clock=lambda: STARTED,
        )
    lock = FakeRunLock()
    result = run_archive(
        FakeBucket("source"),
        FakeBucket("destination"),
        ArchiveOptions(retention_days=60),
        run_started_at_utc=STARTED,
        run_lock=lock,
        clock=lambda: STARTED,
    )
    assert result.ok is True
    assert lock.released == [result.run_id]


@pytest.mark.unit()
def test_run_archive_reports_timeout_after_copy_and_verify_phases() -> None:
    source = FakeBucket("source", (_listed("old.txt", 90),))
    batch_timeout = run_archive(
        source,
        FakeBucket("destination"),
        ArchiveOptions(retention_days=60),
        run_started_at_utc=STARTED,
        clock=SequenceClock(expire_after_calls=1),
    )
    assert batch_timeout.copy.failures == ("archive run timed out",)
    assert batch_timeout.verify.skipped is True
    copy_timeout = run_archive(
        source,
        FakeBucket("destination"),
        ArchiveOptions(retention_days=60),
        run_started_at_utc=STARTED,
        clock=SequenceClock(expire_after_calls=3),
    )
    assert copy_timeout.copy.failures == ("archive run timed out",)
    assert copy_timeout.verify.skipped is True
    verify_timeout = run_archive(
        source,
        FakeBucket("destination"),
        ArchiveOptions(retention_days=60),
        run_started_at_utc=STARTED,
        clock=SequenceClock(expire_after_calls=6),
    )
    assert verify_timeout.copy.ok is True
    assert verify_timeout.verify.failures == ("archive run timed out",)
    assert verify_timeout.cleanup.skipped is True


@pytest.mark.unit()
def test_run_archive_reports_timeout_after_cleanup_phase() -> None:
    source = FakeBucket("source", (_listed("old.txt", 90),))
    result = run_archive(
        source,
        FakeBucket("destination"),
        ArchiveOptions(retention_days=60, cleanup_enabled=True),
        run_started_at_utc=STARTED,
        clock=SequenceClock(expire_after_calls=9),
    )
    assert result.copy.ok is True
    assert result.verify.ok is True
    assert result.cleanup.failures == ("archive run timed out",)


@pytest.mark.unit()
def test_verify_failure_after_copy_blocks_cleanup() -> None:
    class VanishingDestination(FakeBucket):
        @override
        def copy_from(
            self,
            source: object,
            source_bucket: str,
            source_key: str,
            source_version_id: str | None,
            properties: S3ObjectProperties,
            destination_key: str,
            destination_metadata: Mapping[str, str],
            strategy: TransferStrategy,
        ) -> None:
            _ = (
                source,
                source_bucket,
                source_version_id,
                properties,
                destination_key,
                destination_metadata,
                strategy,
            )
            self.copied.append(source_key)
    source = FakeBucket("source", (_listed("old.txt", 90),))
    result = run_archive(
        source,
        VanishingDestination("destination"),
        ArchiveOptions(retention_days=60, cleanup_enabled=True),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )
    assert result.copy.ok is True
    assert result.verify.failures == ("old.txt: destination missing",)
    assert result.cleanup.skipped is True


@pytest.mark.unit()
def test_worker_future_exception_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken_call_worker(
        worker: Callable[[ManifestEntry], str | None], entry: ManifestEntry
    ) -> str | None:
        _ = (worker, entry)
        raise RuntimeError("executor failed")
    monkeypatch.setattr("s3_archiver_core.archive_workers._call_worker", broken_call_worker)
    result = run_archive(
        FakeBucket("source", (_listed("old.txt", 90),)),
        FakeBucket("destination"),
        ArchiveOptions(retention_days=60),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )
    assert result.copy.failures == ("worker failure: executor failed",)
    assert result.verify.skipped is True


@pytest.mark.unit()
def test_rerun_uses_archived_version_for_cleanup() -> None:
    archived = _listed("old.txt", 95, "v1")
    current = replace(_listed("old.txt", 90, "v2"), properties=archived.properties)
    source = FakeBucket(
        "source",
        (current,),
        versions=(archived,),
        version_payloads={("old.txt", "v1"): b"archived", ("old.txt", "v2"): b"current"},
    )
    archived_entry = ManifestEntry(
        "source", "old.txt", 10, archived.last_modified, '"etag"', "v1", archived
    )
    destination = FakeBucket(
        "destination",
        destination={
            "old.txt": replace(archived.properties, metadata=archive_metadata(archived_entry))
        },
        payloads={"old.txt": b"archived"},
    )
    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )
    assert result.manifest.entries[0].version_id == "v2"
    assert result.ok is True
    assert destination.copied == []
    assert source.deleted == [("old.txt", "v1")]


@pytest.mark.unit()
def test_verify_destination_reports_each_mismatch_detail() -> None:
    entry = _entry()
    destination = replace(entry.object.properties, metadata=archive_metadata(entry))
    assert verify_destination(entry, None).detail == "destination missing"
    assert (
        verify_destination(entry, replace(destination, content_type="application/json")).detail
        == "object property mismatch"
    )
    assert (
        verify_destination(
            entry,
            replace(destination, metadata=dict(destination.metadata) | {"owner": "other"}),
        ).detail
        == "metadata mismatch"
    )
    assert (
        verify_destination(entry, replace(destination, tags={"kind": "other"})).detail
        == "tag mismatch"
    )


@pytest.mark.unit()
def test_verify_source_unchanged_reports_missing_and_property_changes() -> None:
    entry = _entry()
    current = entry.object.properties
    assert verify_source_unchanged(entry, None).detail == "source missing before cleanup"
    assert verify_source_unchanged(entry, replace(current, size=11)).ok is False
    assert verify_source_unchanged(entry, replace(current, content_type=None)).ok is False
    assert verify_source_unchanged(entry, replace(current, metadata={"owner": "other"})).ok is False
    assert verify_source_unchanged(entry, replace(current, tags={"kind": "other"})).ok is False
    assert verify_source_unchanged(entry, current).ok is True


@pytest.mark.unit()
def test_fingerprint_metadata_rejects_invalid_shapes() -> None:
    metadata_key = FINGERPRINT_METADATA_KEY
    assert fingerprint_from_metadata({metadata_key: "not-json"}) is None
    assert fingerprint_from_metadata({metadata_key: "[]"}) is None
    assert fingerprint_from_metadata({metadata_key: '{"source_bucket": 3}'}) is None
    assert (
        fingerprint_from_metadata(
            {
                metadata_key: (
                    '{"source_bucket":"bucket","source_key":"key","source_size":1,'
                    '"source_last_modified":"time","source_version_id":3,"source_etag":4}'
                )
            }
        )
        is not None
    )


@pytest.mark.unit()
def test_list_failure_with_lock_still_releases_lock() -> None:
    class BrokenListBucket(FakeBucket):
        @override
        def versioning_state(self) -> VersioningState:
            raise RuntimeError("list failed")
    lock = FakeRunLock()
    result = run_archive(
        BrokenListBucket("source"),
        FakeBucket("destination"),
        ArchiveOptions(retention_days=60),
        run_started_at_utc=STARTED,
        run_lock=lock,
    )
    assert result.list.failures == ("list failed",)
    assert lock.released == [result.run_id]
