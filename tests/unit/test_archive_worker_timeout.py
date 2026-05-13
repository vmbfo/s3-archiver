"""Tests for archive worker timeout behavior."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from datetime import UTC, datetime, timedelta

import pytest
from s3_archiver_core.archive import run_archive

from tests.unit.archive_workflow_fakes import FakeBucket, archive_routes, daily_run_timeout
from tests.unit.archive_workflow_fakes import listed_object as _listed


@pytest.mark.unit()
def test_run_archive_returns_quickly_after_copy_and_verify() -> None:
    started = datetime.now(tz=UTC)
    target_day = started.date() - timedelta(days=60)
    source_key = f"data/fae/{target_day.isoformat()}T00-00-00.txt"
    source = FakeBucket("source", (_listed(source_key, 90),))
    destination = FakeBucket("destination")

    def clock() -> datetime:
        return datetime.now(tz=UTC)

    began = time.monotonic()

    result = run_archive(
        archive_routes(source, destination),
        run_timeout=daily_run_timeout(run_timeout=timedelta(milliseconds=50)),
        run_started_at_utc=started,
        clock=clock,
    )
    assert result.ok is True
    assert time.monotonic() - began < 0.1


@pytest.mark.unit()
def test_timed_out_worker_does_not_keep_python_process_alive() -> None:
    script = textwrap.dedent(
        """
        import time
        from collections.abc import Mapping
        from datetime import UTC, datetime, timedelta
        from pathlib import Path
        from typing import override
        from tests.unit.archive_workflow_fakes import (
            FakeBucket,
            archive_routes, daily_run_timeout,
            listed_object,
        )
        from s3_archiver_core.archive import run_archive

        class StuckUploadBucket(FakeBucket):
            @override
            def upload_archive_file(
                self, destination_key: str, archive_path: Path, metadata: Mapping[str, str]
            ) -> None:
                time.sleep(5)

        started = datetime.now(tz=UTC)
        target_day = started.date() - timedelta(days=60)
        source_key = f"data/fae/{target_day.isoformat()}T00-00-00.txt"
        source = FakeBucket("source", (listed_object(source_key, 90),))
        destination = StuckUploadBucket("destination")
        result = run_archive(
            archive_routes(source, destination),
            run_timeout=daily_run_timeout(run_timeout=timedelta(milliseconds=50)),
            run_started_at_utc=started,
            clock=lambda: datetime.now(tz=UTC),
        )
        assert result.copy.failures == ("archive run timed out",)
        """
    )

    _ = subprocess.run([sys.executable, "-c", script], check=True, timeout=1.0)
