"""Regression tests for destructive refreshes and concurrent source replacement."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest
from s3_archiver_core.archive import ArchiveRunResult, run_archive
from s3_archiver_core.archive_membership import MEMBERSHIP_METADATA_KEY
from s3_archiver_core.s3 import S3ListedObject

from tests.unit.archive_workflow_fakes import FakeBucket, archive_routes, daily_run_timeout
from tests.unit.archive_workflow_fakes import listed_object as listed

STARTED = datetime(2026, 5, 22, 7, tzinfo=UTC)
KEY = "data/model/2026-05-21.tar.gz"


def run(source: FakeBucket, destination: FakeBucket) -> ArchiveRunResult:
    return run_archive(
        archive_routes(source, destination),
        run_timeout=daily_run_timeout(),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )


def objects(*hours: int) -> tuple[S3ListedObject, ...]:
    return tuple(listed(f"data/model/2026-05-21T{hour:02}-00-00Z.grib", 1) for hour in hours)


@pytest.mark.unit()
@pytest.mark.parametrize("late_hours", [(6,), (6, 9), (6, 9, 12)])
def test_deleted_members_never_overwritten_even_with_more_late_arrivals(
    tmp_path: Path, late_hours: tuple[int, ...]
) -> None:
    original = objects(0, 3)
    source = FakeBucket("source", original, temp_dir=tmp_path)
    destination = FakeBucket("destination", temp_dir=tmp_path)
    assert run(source, destination).ok
    payload = destination.destination_payload(KEY)
    for entry in original:
        source.delete_source_object(entry.key, entry.version_id)
    late = FakeBucket("source", objects(*late_hours), temp_dir=tmp_path)
    result = run(late, destination)
    assert not result.ok
    assert result.cleanup_entries is not None and len(result.cleanup_entries) == 0
    assert destination.destination_payload(KEY) == payload
    assert destination.uploaded == [KEY]
    assert late.deleted == []


@pytest.mark.unit()
@pytest.mark.parametrize("change", ["version", "etag", "size", "key", "source"])
def test_same_count_replacements_never_authorize_refresh(tmp_path: Path, change: str) -> None:
    original = objects(0)[0]
    destination = FakeBucket("destination", temp_dir=tmp_path)
    assert run(FakeBucket("source", (original,), temp_dir=tmp_path), destination).ok
    old = destination.destination_payload(KEY)
    updated = original
    bucket = "source"
    if change == "version":
        updated = replace(original, version_id="new")
    elif change == "etag":
        updated = replace(
            original, etag='"new"', properties=replace(original.properties, etag='"new"')
        )
    elif change == "size":
        updated = replace(original, size=11, properties=replace(original.properties, size=11))
    elif change == "key":
        updated = objects(6)[0]
    else:
        bucket = "another-source"
    result = run(FakeBucket(bucket, (updated,), temp_dir=tmp_path), destination)
    assert not result.ok
    assert destination.destination_payload(KEY) == old


@pytest.mark.unit()
@pytest.mark.parametrize("damage", ["missing", "truncated", "forged", "legacy"])
def test_missing_or_invalid_membership_fails_closed(tmp_path: Path, damage: str) -> None:
    destination = FakeBucket("destination", temp_dir=tmp_path)
    assert run(FakeBucket("source", objects(0), temp_dir=tmp_path), destination).ok
    old = destination.destination_payload(KEY)
    props = destination._destination[KEY]
    sidecar = props.metadata[MEMBERSHIP_METADATA_KEY]
    if damage == "missing":
        del destination._destination_payloads[sidecar]
    elif damage == "legacy":
        metadata = dict(props.metadata)
        del metadata[MEMBERSHIP_METADATA_KEY]
        destination._destination[KEY] = replace(props, metadata=metadata)
    elif damage == "forged":
        destination._destination_payloads[sidecar] = b"garbage"
    else:
        destination._destination_payloads[sidecar] = destination.destination_payload(sidecar)[:-12]
    result = run(FakeBucket("source", objects(0, 3), temp_dir=tmp_path), destination)
    assert not result.ok
    assert destination.destination_payload(KEY) == old


class TruncatingDestination(FakeBucket):
    @override
    def upload_archive_file(
        self, destination_key: str, archive_path: Path, metadata: Mapping[str, str]
    ) -> None:
        super().upload_archive_file(destination_key, archive_path, metadata)
        if destination_key == KEY:
            self._destination_payloads[KEY] = self._destination_payloads[KEY][:-1]
            self._destination[KEY] = replace(
                self._destination[KEY], size=archive_path.stat().st_size - 1
            )


@pytest.mark.unit()
def test_truncated_upload_never_qualifies_for_cleanup_or_reuse(tmp_path: Path) -> None:
    destination = TruncatingDestination("destination", temp_dir=tmp_path)
    source = FakeBucket("source", objects(0), temp_dir=tmp_path)
    for _ in range(2):
        result = run(source, destination)
        assert not result.ok
        assert "size mismatch" in result.copy.failures[0]
        assert result.cleanup_entries is not None and len(result.cleanup_entries) == 0
    assert len(destination.uploaded) == 2


@pytest.mark.unit()
@pytest.mark.parametrize("length", [9, 11])
def test_member_stream_must_match_listed_size(tmp_path: Path, length: int) -> None:
    entry = objects(0)[0]
    source = FakeBucket("source", (entry,), payloads={entry.key: b"x" * length}, temp_dir=tmp_path)
    destination = FakeBucket("destination", temp_dir=tmp_path)
    result = run(source, destination)
    assert not result.ok
    assert destination.uploaded == []


@pytest.mark.unit()
def test_etag_replacement_after_listing_aborts_before_archive_upload(tmp_path: Path) -> None:
    entry = replace(objects(0)[0], version_id=None)
    source = FakeBucket("source", (entry,), versioning_state="Disabled", temp_dir=tmp_path)
    source._objects[entry.key] = replace(entry, properties=replace(entry.properties, etag='"new"'))
    destination = FakeBucket("destination", temp_dir=tmp_path)
    result = run(source, destination)
    assert not result.ok
    assert "read precondition failed" in result.copy.failures[0]
    assert destination.uploaded == []


@pytest.mark.unit()
def test_unversioned_source_without_etag_is_not_archived(tmp_path: Path) -> None:
    entry = replace(objects(0)[0], version_id=None, etag=None)
    source = FakeBucket("source", (entry,), versioning_state="Disabled", temp_dir=tmp_path)
    destination = FakeBucket("destination", temp_dir=tmp_path)
    result = run(source, destination)
    assert not result.ok
    assert "unversioned source has no ETag" in result.copy.failures[0]
    assert destination.uploaded == []
