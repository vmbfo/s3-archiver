from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Protocol, final, overload, override

from s3_archiver_core._archive_manifest_models import ArchiveGroup, ManifestEntry, SkippedObject
from s3_archiver_core._archive_manifest_slices import slice_items
from s3_archiver_core._archive_manifest_sqlite import normalize_index


class _ManifestStore(Protocol):
    def entry_count(self) -> int: ...

    def entry_count_by_copy_mode(self, copy_mode: str) -> int: ...

    def skipped_count(self) -> int: ...

    def group_count(self) -> int: ...

    def target_days(self) -> tuple[str, ...]: ...

    def iter_entries(self) -> Iterator[ManifestEntry]: ...

    def iter_route_entries(
        self,
        route_name: str,
        copy_mode: str | None = None,
    ) -> Iterator[ManifestEntry]: ...

    def entry_at(self, index: int) -> ManifestEntry: ...

    def iter_skipped(self) -> Iterator[SkippedObject]: ...

    def skipped_at(self, index: int) -> SkippedObject: ...

    def iter_groups(self, route_name: str | None = None) -> Iterator[ArchiveGroup]: ...

    def group_at(self, index: int) -> ArchiveGroup: ...


@final
class EntrySequence(Sequence[ManifestEntry]):
    def __init__(self, store: _ManifestStore) -> None:
        self._store: _ManifestStore = store

    @override
    def __len__(self) -> int:
        return self._store.entry_count()

    @overload
    def __getitem__(self, index: int) -> ManifestEntry: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ManifestEntry, ...]: ...

    @override
    def __getitem__(self, index: int | slice) -> ManifestEntry | tuple[ManifestEntry, ...]:
        if isinstance(index, slice):
            return slice_items(self, index)
        return self._store.entry_at(normalize_index(index, len(self)))

    @override
    def __iter__(self) -> Iterator[ManifestEntry]:
        return self._store.iter_entries()

    def iter_route(self, route_name: str, copy_mode: str | None = None) -> Iterator[ManifestEntry]:
        return self._store.iter_route_entries(route_name, copy_mode)

    def count_copy_mode(self, copy_mode: str) -> int:
        return self._store.entry_count_by_copy_mode(copy_mode)


@final
class SkippedSequence(Sequence[SkippedObject]):
    def __init__(self, store: _ManifestStore) -> None:
        self._store: _ManifestStore = store

    @override
    def __len__(self) -> int:
        return self._store.skipped_count()

    @overload
    def __getitem__(self, index: int) -> SkippedObject: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[SkippedObject, ...]: ...

    @override
    def __getitem__(self, index: int | slice) -> SkippedObject | tuple[SkippedObject, ...]:
        if isinstance(index, slice):
            return slice_items(self, index)
        return self._store.skipped_at(normalize_index(index, len(self)))

    @override
    def __iter__(self) -> Iterator[SkippedObject]:
        return self._store.iter_skipped()


@final
class ArchiveGroupSequence(Sequence[ArchiveGroup]):
    def __init__(self, store: _ManifestStore) -> None:
        self._store: _ManifestStore = store

    @override
    def __len__(self) -> int:
        return self._store.group_count()

    @overload
    def __getitem__(self, index: int) -> ArchiveGroup: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ArchiveGroup, ...]: ...

    @override
    def __getitem__(self, index: int | slice) -> ArchiveGroup | tuple[ArchiveGroup, ...]:
        if isinstance(index, slice):
            return slice_items(self, index)
        return self._store.group_at(normalize_index(index, len(self)))

    @override
    def __iter__(self) -> Iterator[ArchiveGroup]:
        return self._store.iter_groups()

    def iter_route(self, route_name: str) -> Iterator[ArchiveGroup]:
        return self._store.iter_groups(route_name)

    def target_days(self) -> tuple[str, ...]:
        return self._store.target_days()
