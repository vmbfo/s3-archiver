"""Unit coverage for canonical retention dataset splits."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from s3_archiver_core.archive import run_archive
from s3_archiver_core.archive_options import ArchiveOptions

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2024, 4, 20, tzinfo=UTC)
CANONICAL_DAYS = tuple(range(366))


def _clock() -> datetime:
    return STARTED


def _canonical_source(prefix: str) -> FakeBucket:
    return FakeBucket(
        "source",
        tuple(_listed(f"{prefix}/age-{day}-days.txt", day) for day in CANONICAL_DAYS),
    )


@pytest.mark.unit()
@pytest.mark.parametrize(
    ("retention_days", "cleanup_enabled"),
    [(60, False), (60, True), (30, False)],
)
def test_canonical_retention_dataset_has_exact_archive_split(
    retention_days: int,
    cleanup_enabled: bool,
) -> None:
    prefix = f"retention-canonical/{retention_days}-{'cleanup' if cleanup_enabled else 'keep'}"
    source = _canonical_source(prefix)
    destination = FakeBucket("destination")

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=retention_days, cleanup_enabled=cleanup_enabled),
        run_started_at_utc=STARTED,
        clock=_clock,
    )

    archived_days = {day for day in CANONICAL_DAYS if day > retention_days}
    expected_keys = {f"{prefix}/age-{day}-days.txt" for day in archived_days}

    assert result.ok is True
    assert len(result.manifest.entries) == len(archived_days)
    assert set(destination.copied) == expected_keys
    if cleanup_enabled:
        assert {key for key, _version_id in source.deleted} == expected_keys
    else:
        assert source.deleted == []
