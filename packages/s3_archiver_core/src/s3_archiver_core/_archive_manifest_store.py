from __future__ import annotations

import pickle
import sqlite3
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import suppress
from pathlib import Path
from typing import overload

from s3_archiver_core._archive_identity import stable_identity_value
from s3_archiver_core._archive_manifest_models import ArchiveGroup, ManifestEntry, SkippedObject


class SQLiteManifestStore:
    """Disk-backed archive manifest rows."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA journal_mode=OFF")
        self._connection.execute("PRAGMA synchronous=OFF")
        self._create_schema()

    @classmethod
    def temporary(cls, directory: Path | None = None) -> SQLiteManifestStore:
        temp = tempfile.NamedTemporaryFile(
            prefix="s3-archiver-manifest-",
            suffix=".sqlite3",
            dir=None if directory is None else directory,
            delete=False,
        )
        temp.close()
        return cls(Path(temp.name))

    @property
    def entries(self) -> EntrySequence:
        return EntrySequence(self)

    @property
    def archive_groups(self) -> ArchiveGroupSequence:
        return ArchiveGroupSequence(self)

    @property
    def skipped_objects(self) -> SkippedSequence:
        return SkippedSequence(self)

    def add_entry(self, entry: ManifestEntry) -> None:
        self._connection.execute(
            """
            INSERT INTO entries (
                route_name, copy_mode, source_identity, source_bucket, key, version_id,
                destination_identity, destination_bucket, destination_key,
                destination_archive_key, target_day, archive_root, size, payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.route_name,
                entry.copy_mode,
                _stable_key(entry.source_identity),
                entry.source_bucket,
                entry.key,
                entry.version_id or "",
                _stable_key(entry.destination_identity),
                entry.destination_bucket,
                entry.destination_key,
                entry.destination_archive_key,
                "" if entry.target_day is None else entry.target_day.isoformat(),
                entry.archive_root,
                entry.size,
                _pack(entry),
            ),
        )

    def add_skipped(self, skipped: SkippedObject) -> None:
        self._connection.execute("INSERT INTO skipped (payload) VALUES (?)", (_pack(skipped),))

    def commit(self) -> None:
        self._connection.commit()

    def assert_no_duplicate_sources(self) -> None:
        row = self._connection.execute(
            """
            SELECT source_identity, source_bucket, key, version_id
            FROM entries
            GROUP BY source_identity, source_bucket, key, version_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            raise ValueError("duplicate source object identity")

    def assert_no_duplicate_destinations(self) -> None:
        row = self._connection.execute(
            """
            WITH destinations AS (
                SELECT destination_identity, destination_bucket, destination_key AS key
                FROM entries
                WHERE copy_mode = 'direct'
                UNION ALL
                SELECT destination_identity, destination_bucket, destination_archive_key AS key
                FROM entries
                WHERE copy_mode = 'daily_tar_gz' AND target_day != ''
                GROUP BY destination_identity, destination_bucket, destination_archive_key
            )
            SELECT destination_identity, destination_bucket, key
            FROM destinations
            GROUP BY destination_identity, destination_bucket, key
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            raise ValueError("duplicate destination object identity")
        seen_groups: set[tuple[str, str, str]] = set()
        for group in self.iter_groups():
            group_key = (
                _stable_key(group.destination_identity),
                group.destination_bucket,
                group.destination_archive_key,
            )
            if group_key in seen_groups:
                raise ValueError("duplicate destination object identity")
            seen_groups.add(group_key)
            direct_collision = self._connection.execute(
                """
                SELECT 1
                FROM entries
                WHERE copy_mode = 'direct'
                AND destination_identity = ?
                AND destination_bucket = ?
                AND destination_key = ?
                LIMIT 1
                """,
                group_key,
            ).fetchone()
            if direct_collision is not None:
                raise ValueError("duplicate destination object identity")

    def entry_count(self) -> int:
        return _count(self._connection, "entries")

    def entry_count_by_copy_mode(self, copy_mode: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM entries WHERE copy_mode = ?",
            (copy_mode,),
        ).fetchone()
        return int(row[0])

    def skipped_count(self) -> int:
        return _count(self._connection, "skipped")

    def group_count(self) -> int:
        return sum(1 for _group in self.iter_groups())

    def entry_size_sum(self) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(SUM(size), 0) FROM entries",
        ).fetchone()
        return int(row[0])

    def iter_entries(
        self,
        where: str = "",
        params: tuple[object, ...] = (),
        *,
        order_by: str = "id",
    ) -> Iterator[ManifestEntry]:
        query = "SELECT payload FROM entries"
        if where:
            query += f" WHERE {where}"
        query += f" ORDER BY {order_by}"
        for (payload,) in self._connection.execute(query, params):
            yield _unpack(payload)

    def entry_at(self, index: int) -> ManifestEntry:
        return _unpack(
            _single_value(
                self._connection,
                "SELECT payload FROM entries ORDER BY id LIMIT 1 OFFSET ?",
                (index,),
            )
        )

    def iter_skipped(self) -> Iterator[SkippedObject]:
        for (payload,) in self._connection.execute("SELECT payload FROM skipped ORDER BY id"):
            yield _unpack(payload)

    def skipped_at(self, index: int) -> SkippedObject:
        return _unpack(
            _single_value(
                self._connection,
                "SELECT payload FROM skipped ORDER BY id LIMIT 1 OFFSET ?",
                (index,),
            )
        )

    def iter_groups(self, route_name: str | None = None) -> Iterator[ArchiveGroup]:
        params: tuple[object, ...] = ()
        where = "copy_mode = 'daily_tar_gz' AND target_day != ''"
        if route_name is not None:
            where += " AND route_name = ?"
            params = (route_name,)
        rows = self._connection.execute(
            """
            SELECT route_name, archive_root, target_day, destination_bucket,
                destination_archive_key, destination_identity
            FROM entries
            WHERE """ + where + """
            GROUP BY route_name, archive_root, target_day, destination_bucket,
                destination_archive_key, destination_identity
            ORDER BY route_name, archive_root, target_day, destination_bucket,
                destination_archive_key, destination_identity
            """,
            params,
        )
        for row in rows:
            yield from self._groups_from_row(row)

    def group_at(self, index: int) -> ArchiveGroup:
        normalized = _normalize_index(index, self.group_count())
        for offset, group in enumerate(self.iter_groups()):
            if offset == normalized:
                return group
        raise IndexError(index)

    def _groups_from_row(self, row: sqlite3.Row | tuple[object, ...]) -> Iterator[ArchiveGroup]:
        from s3_archiver_core._archive_manifest_builder import (
            DEFAULT_ARCHIVE_GROUP_MAX_BYTES,
            DEFAULT_ARCHIVE_GROUP_MAX_OBJECTS,
            _archive_chunk_entries,
            _positive_int_env,
        )

        route_name, archive_root, target_day, destination_bucket, destination_key, identity = row
        max_bytes = _positive_int_env(
            "ARCHIVER_ARCHIVE_GROUP_MAX_BYTES", DEFAULT_ARCHIVE_GROUP_MAX_BYTES
        )
        max_objects = _positive_int_env(
            "ARCHIVER_ARCHIVE_GROUP_MAX_OBJECTS", DEFAULT_ARCHIVE_GROUP_MAX_OBJECTS
        )
        chunk: list[ManifestEntry] = []
        chunk_bytes = 0
        chunk_index = 1
        for entry in self.iter_entries(
            """
            route_name = ? AND archive_root = ? AND target_day = ?
            AND destination_bucket = ? AND destination_archive_key = ?
            AND destination_identity = ? AND copy_mode = 'daily_tar_gz'
            """,
            (route_name, archive_root, target_day, destination_bucket, destination_key, identity),
            order_by="key",
        ):
            next_bytes = chunk_bytes + max(entry.size, 0)
            if chunk and (len(chunk) >= max_objects or next_bytes > max_bytes):
                yield _group_for_chunk(tuple(chunk), chunk_index, _archive_chunk_entries)
                chunk = []
                chunk_bytes = 0
                chunk_index += 1
                next_bytes = max(entry.size, 0)
            chunk.append(entry)
            chunk_bytes = next_bytes
        if chunk:
            yield _group_for_chunk(tuple(chunk), chunk_index, _archive_chunk_entries)

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY,
                route_name TEXT NOT NULL,
                copy_mode TEXT NOT NULL,
                source_identity TEXT NOT NULL,
                source_bucket TEXT NOT NULL,
                key TEXT NOT NULL,
                version_id TEXT NOT NULL,
                destination_identity TEXT NOT NULL,
                destination_bucket TEXT NOT NULL,
                destination_key TEXT NOT NULL,
                destination_archive_key TEXT NOT NULL,
                target_day TEXT NOT NULL,
                archive_root TEXT NOT NULL,
                size INTEGER NOT NULL,
                payload BLOB NOT NULL
            );
            CREATE TABLE skipped (
                id INTEGER PRIMARY KEY,
                payload BLOB NOT NULL
            );
            CREATE INDEX entries_route_copy_idx ON entries(route_name, copy_mode);
            CREATE INDEX entries_group_idx ON entries(
                route_name, archive_root, target_day, destination_bucket,
                destination_archive_key, destination_identity
            );
            """
        )

    def close(self) -> None:
        self._connection.close()

    def cleanup(self) -> None:
        with suppress(Exception):
            self.close()
        self.path.unlink(missing_ok=True)

    def __del__(self) -> None:
        self.cleanup()


class EntrySequence(Sequence[ManifestEntry]):
    def __init__(self, store: SQLiteManifestStore) -> None:
        self._store = store

    def __len__(self) -> int:
        return self._store.entry_count()

    @overload
    def __getitem__(self, index: int) -> ManifestEntry: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ManifestEntry, ...]: ...

    def __getitem__(self, index: int | slice) -> ManifestEntry | tuple[ManifestEntry, ...]:
        if isinstance(index, slice):
            return tuple(self)[index]
        return self._store.entry_at(_normalize_index(index, len(self)))

    def __iter__(self) -> Iterator[ManifestEntry]:
        return self._store.iter_entries()

    def iter_route(
        self, route_name: str, copy_mode: str | None = None
    ) -> Iterator[ManifestEntry]:
        where = "route_name = ?"
        params: tuple[object, ...] = (route_name,)
        if copy_mode is not None:
            where += " AND copy_mode = ?"
            params = (route_name, copy_mode)
        return self._store.iter_entries(where, params)

    def count_copy_mode(self, copy_mode: str) -> int:
        return self._store.entry_count_by_copy_mode(copy_mode)


class SkippedSequence(Sequence[SkippedObject]):
    def __init__(self, store: SQLiteManifestStore) -> None:
        self._store = store

    def __len__(self) -> int:
        return self._store.skipped_count()

    @overload
    def __getitem__(self, index: int) -> SkippedObject: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[SkippedObject, ...]: ...

    def __getitem__(self, index: int | slice) -> SkippedObject | tuple[SkippedObject, ...]:
        if isinstance(index, slice):
            return tuple(self)[index]
        return self._store.skipped_at(_normalize_index(index, len(self)))

    def __iter__(self) -> Iterator[SkippedObject]:
        return self._store.iter_skipped()


class ArchiveGroupSequence(Sequence[ArchiveGroup]):
    def __init__(self, store: SQLiteManifestStore) -> None:
        self._store = store

    def __len__(self) -> int:
        return self._store.group_count()

    @overload
    def __getitem__(self, index: int) -> ArchiveGroup: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[ArchiveGroup, ...]: ...

    def __getitem__(self, index: int | slice) -> ArchiveGroup | tuple[ArchiveGroup, ...]:
        if isinstance(index, slice):
            return tuple(self)[index]
        return self._store.group_at(_normalize_index(index, len(self)))

    def __iter__(self) -> Iterator[ArchiveGroup]:
        return self._store.iter_groups()

    def iter_route(self, route_name: str) -> Iterator[ArchiveGroup]:
        return self._store.iter_groups(route_name)


def _stable_key(value: object) -> str:
    return repr(stable_identity_value(value))


def _pack(value: object) -> bytes:
    return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)


def _unpack[T](value: bytes) -> T:
    return pickle.loads(value)


def _count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0])


def _single_value(
    connection: sqlite3.Connection,
    query: str,
    params: tuple[object, ...],
) -> bytes:
    row = connection.execute(query, params).fetchone()
    if row is None:
        raise IndexError(params[0])
    return row[0]


def _normalize_index(index: int, length: int) -> int:
    normalized = length + index if index < 0 else index
    if normalized < 0 or normalized >= length:
        raise IndexError(index)
    return normalized


def _group_for_chunk(
    entries: tuple[ManifestEntry, ...],
    chunk_index: int,
    chunk_entries: Callable[[tuple[ManifestEntry, ...], int], tuple[ManifestEntry, ...]],
) -> ArchiveGroup:
    chunked = chunk_entries(entries, chunk_index)
    first = chunked[0]
    if first.target_day is None:  # pragma: no cover - guarded before grouping
        raise RuntimeError("daily archive group missing target day")
    return ArchiveGroup(
        target_day=first.target_day,
        archive_root=first.archive_root,
        destination_archive_key=first.destination_archive_key,
        entries=chunked,
        route_name=first.route_name,
        parser_kind=first.parser_kind,
        copy_mode=first.copy_mode,
        source_bucket=first.source_bucket,
        source_identity=first.source_identity,
        destination_bucket=first.destination_bucket,
        destination_identity=first.destination_identity,
    )
