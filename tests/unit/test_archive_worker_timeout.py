"""Tests for archive worker timeout behavior."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from typing import cast, override

import pytest
from s3_archiver_core.archive import ArchiveRunResult, run_archive
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_transfer import TransferStrategy
from s3_archiver_core.s3 import S3ObjectProperties

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed


@pytest.mark.unit()
def test_run_archive_waits_for_active_copy_worker_before_timeout_returns() -> None:
    class SlowCopyBucket(FakeBucket):
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
                source_key,
                source_version_id,
                properties,
                destination_key,
                destination_metadata,
                strategy,
            )
            time.sleep(0.2)
            self.copied.append(source_key)

    source = FakeBucket("source", (_listed("slow.txt", 90),))
    destination = SlowCopyBucket("destination")
    started = datetime.now(tz=UTC)

    def clock() -> datetime:
        return datetime.now(tz=UTC)

    began = time.monotonic()

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, run_timeout=timedelta(milliseconds=50)),
        run_started_at_utc=started,
        clock=clock,
    )

    assert result.copy.failures == ("archive run timed out",)
    assert time.monotonic() - began >= 0.18


@pytest.mark.unit()
def test_run_archive_releases_lock_after_timed_out_copy_worker_finishes() -> None:
    copy_started = Event()
    allow_copy_exit = Event()
    copy_finished = Event()

    class RecordingRunLock:
        def __init__(self) -> None:
            self.released: list[str] = []

        def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: timedelta) -> bool:
            _ = (run_id, run_started_at_utc, timeout)
            return True

        def release(self, *, run_id: str) -> None:
            self.released.append(run_id)

    class BlockingCopyBucket(FakeBucket):
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
                source_key,
                source_version_id,
                properties,
                destination_key,
                destination_metadata,
                strategy,
            )
            copy_started.set()
            try:
                assert allow_copy_exit.wait(timeout=5)
            finally:
                copy_finished.set()

    lock = RecordingRunLock()
    result: dict[str, object] = {}

    def run() -> None:
        result["value"] = run_archive(
            FakeBucket("source", (_listed("slow.txt", 90),)),
            BlockingCopyBucket("destination"),
            ArchiveOptions(retention_days=60, run_timeout=timedelta(milliseconds=50)),
            run_started_at_utc=datetime.now(tz=UTC),
            run_lock=lock,
            clock=lambda: datetime.now(tz=UTC),
        )

    archive_thread = Thread(target=run)
    archive_thread.start()

    assert copy_started.wait(timeout=5)
    time.sleep(0.1)
    assert lock.released == []

    allow_copy_exit.set()
    assert copy_finished.wait(timeout=5)
    archive_thread.join(timeout=5)

    assert not archive_thread.is_alive()
    archive_result = cast(ArchiveRunResult, result["value"])
    assert archive_result.copy.failures == ("archive run timed out",)
    assert lock.released == [archive_result.run_id]
