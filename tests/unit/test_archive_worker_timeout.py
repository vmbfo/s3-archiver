"""Tests for archive worker timeout behavior."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import override

import pytest
from s3_archiver_core.archive import run_archive
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_transfer import TransferStrategy
from s3_archiver_core.s3 import S3ObjectProperties

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed


@pytest.mark.unit()
def test_run_archive_returns_without_waiting_for_timed_out_copy_worker() -> None:
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
    assert time.monotonic() - began < 0.15
