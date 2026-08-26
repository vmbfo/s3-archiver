"""Cursor-consumption invariants for the oversized-group size filter.

``_archive_size_filter_needed`` runs between the manifest build commit and
``drop_oversized_groups``. Groups read there stream entries from a SQLite
reader connection, and a reader statement abandoned mid-result-set holds a
shared lock that makes the drop's write-connection commit fail with
``sqlite3.OperationalError: database is locked``. The filter therefore must
sum every entry of each group it reads instead of short-circuiting on the
running total, leaving no suspended cursor behind. These tests pin the full
consumption and demonstrate the failure a suspended cursor causes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path
from typing import cast, final, overload, override

import pytest
from s3_archiver_core._archive_manifest_models import ArchiveGroup, CopyMode, ManifestEntry
from s3_archiver_core._archive_manifest_store import SQLiteManifestStore
from s3_archiver_core._archive_route_manifest import (
    _archive_size_filter_needed,  # pyright: ignore[reportPrivateUsage]
)

from tests.unit.archive_workflow_fakes import listed_object as _listed

DAY = date(2026, 4, 13)
DAY_BIG = date(2026, 4, 14)
_SMALL_SIZE = 1
_BIG_SIZE = 4 * 1024 * 1024
_LIMIT = 2 * 1024 * 1024


@final
class _CursorTrackingEntries(Sequence[ManifestEntry]):
    """Entry sequence that records how far iteration progressed."""

    def __init__(self, entries: tuple[ManifestEntry, ...]) -> None:
        self._entries = entries
        self.yielded = 0

    @override
    def __len__(self) -> int:
        return len(self._entries)

    @overload
    def __getitem__(self, index: int) -> ManifestEntry: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[ManifestEntry]: ...

    @override
    def __getitem__(self, index: int | slice) -> ManifestEntry | Sequence[ManifestEntry]:
        return self._entries[index]

    @override
    def __iter__(self) -> Iterator[ManifestEntry]:
        for entry in self._entries:
            self.yielded += 1
            yield entry

    def fully_consumed(self) -> bool:
        return self.yielded == len(self._entries)

    def partially_consumed(self) -> bool:
        return 0 < self.yielded < len(self._entries)


@pytest.mark.unit()
def test_size_filter_consumes_every_group_when_no_group_is_oversized() -> None:
    first = _CursorTrackingEntries(tuple(_entries("data/a", 3, size=_SMALL_SIZE, target_day=DAY)))
    second = _CursorTrackingEntries(
        tuple(_entries("data/b", 3, size=_SMALL_SIZE, target_day=DAY_BIG))
    )

    assert _archive_size_filter_needed([_group(first, DAY), _group(second, DAY_BIG)]) is False
    assert first.fully_consumed()
    assert second.fully_consumed()


@pytest.mark.unit()
def test_size_filter_fully_consumes_group_that_exceeds_the_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A 1 MiB limit is below the estimate's fixed overhead, so the first
    # group's running total exceeds the limit at its very first entry; the
    # remaining entries must still be consumed to exhaust the cursor.
    monkeypatch.setenv("ARCHIVER_MAX_DESTINATION_ARCHIVE_SIZE_MIB", "1")
    first = _CursorTrackingEntries(tuple(_entries("data/a", 3, size=_SMALL_SIZE, target_day=DAY)))
    second = _CursorTrackingEntries(
        tuple(_entries("data/b", 3, size=_SMALL_SIZE, target_day=DAY_BIG))
    )

    assert _archive_size_filter_needed([_group(first, DAY), _group(second, DAY_BIG)]) is True
    assert first.fully_consumed()
    assert not first.partially_consumed()
    assert not second.partially_consumed()


@pytest.mark.unit()
def test_drop_oversized_groups_commit_fails_under_suspended_reader_cursor(
    tmp_path: Path,
) -> None:
    store = SQLiteManifestStore(tmp_path / "suspended.sqlite3")
    try:
        store.add_entry(_entry("data/a.xml", size=_SMALL_SIZE, target_day=DAY))
        store.add_entry(_entry("data/b.xml", size=_SMALL_SIZE, target_day=DAY))
        store.add_entry(_entry("data/big.xml", size=_BIG_SIZE, target_day=DAY_BIG))
        store.commit()
        _ = store._connection.execute(  # pyright: ignore[reportPrivateUsage]
            "PRAGMA busy_timeout = 100"
        )

        group = next(iter(store.archive_groups))
        suspended = iter(group.entries)
        _ = next(suspended)

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            _ = store.drop_oversized_groups(_LIMIT)
    finally:
        store.cleanup()


def _group(entries: _CursorTrackingEntries, target_day: date) -> ArchiveGroup:
    return ArchiveGroup(
        target_day=target_day,
        archive_root="",
        destination_archive_key=target_day.isoformat(),
        entries=entries,
    )


def _entries(prefix: str, count: int, *, size: int, target_day: date) -> Iterator[ManifestEntry]:
    for index in range(count):
        yield _entry(f"{prefix}-{index}.xml", size=size, target_day=target_day)


def _entry(
    key: str,
    *,
    size: int,
    target_day: date,
    route_name: str = "daily",
    copy_mode: str = "daily_tar_gz",
) -> ManifestEntry:
    listed = _listed(key, 1, key)
    destination_key = target_day.isoformat()
    return ManifestEntry(
        source_bucket="source",
        key=key,
        size=size,
        last_modified=listed.last_modified,
        etag=listed.etag,
        version_id=key,
        object=listed,
        selected_timestamp=listed.last_modified,
        timestamp_source="last_modified",
        target_day=target_day,
        archive_root="",
        destination_archive_key=destination_key,
        route_name=route_name,
        parser_kind="filename_timestamp",
        copy_mode=cast(CopyMode, copy_mode),
        source_path="",
        destination_bucket="archive",
        destination_path="",
        destination_key=destination_key,
        source_identity=("source-id",),
        destination_identity=("destination-id",),
    )
