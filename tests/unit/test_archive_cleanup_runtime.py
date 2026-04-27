"""Cleanup-focused archive runtime tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import override

import pytest
from s3_archiver_core.archive import run_archive
from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_transfer import archive_metadata
from s3_archiver_core.s3 import S3ObjectProperties

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2024, 4, 20, tzinfo=UTC)


def _clock() -> datetime:
    return STARTED


@pytest.mark.unit()
def test_cleanup_requires_destination_fingerprint_before_delete() -> None:
    source_object = _listed("old.txt", 90)
    source = FakeBucket("source", (source_object,))
    archived_entry = ManifestEntry(
        "source",
        "old.txt",
        10,
        source_object.last_modified,
        '"etag"',
        "v1",
        source_object,
    )

    class CleanupMutationBucket(FakeBucket):
        head_calls: int

        def __init__(self) -> None:
            super().__init__(
                "destination",
                destination={
                    "old.txt": replace(
                        archived_entry.object.properties,
                        metadata=archive_metadata(archived_entry),
                    )
                },
            )
            self.head_calls = 0

        @override
        def head_object(self, key: str, version_id: str | None = None) -> S3ObjectProperties | None:
            self.head_calls += 1
            properties = super().head_object(key, version_id)
            if self.head_calls >= 4 and properties is not None:
                return replace(properties, metadata={})
            return properties

    destination = CleanupMutationBucket()

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert result.copy.ok is True
    assert result.verify.ok is True
    assert result.cleanup.failures == (
        "old.txt: destination fingerprint not recoverable before cleanup",
    )
    assert source.deleted == []


@pytest.mark.unit()
def test_cleanup_fails_when_destination_disappears_before_delete() -> None:
    source = FakeBucket("source", (_listed("old.txt", 90),))

    class MissingBeforeCleanupBucket(FakeBucket):
        head_calls: int

        def __init__(self) -> None:
            super().__init__("destination")
            self.head_calls = 0

        @override
        def head_object(self, key: str, version_id: str | None = None) -> S3ObjectProperties | None:
            self.head_calls += 1
            properties = super().head_object(key, version_id)
            if self.head_calls >= 3:
                return None
            return properties

    destination = MissingBeforeCleanupBucket()

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    assert result.copy.ok is True
    assert result.verify.ok is True
    assert result.cleanup.failures == ("old.txt: destination missing before cleanup",)
