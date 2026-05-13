from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from itertools import chain

from s3_archiver_core._archive_identity import stable_identity_value
from s3_archiver_core._archive_manifest_builder import (
    archive_groups,
    iter_archive_manifest_items,
)
from s3_archiver_core._archive_manifest_store import SQLiteManifestStore
from s3_archiver_core._archive_manifest_models import (
    ArchiveGroup,
    ArchiveManifest,
    ArchiveManifestRouteSpec,
    ManifestEntry,
    SkippedObject,
)
from s3_archiver_core._archive_manifest_paths import (
    as_utc,
    route_paths_overlap,
    storage_identity,
)
from s3_archiver_core.s3 import VersioningState

_SQLITE_MANIFEST_ENTRY_THRESHOLD = 100_000


def build_route_archive_manifest(
    routes: Iterable[ArchiveManifestRouteSpec],
    *,
    run_started_at_utc: datetime,
) -> ArchiveManifest:
    """Build a deterministic global manifest for route-based archiving."""

    run_started = as_utc(run_started_at_utc)
    route_tuple = tuple(routes)
    _reject_overlapping_source_paths(route_tuple)
    entries: list[ManifestEntry] = []
    skipped: list[SkippedObject] = []
    store: SQLiteManifestStore | None = None
    for route in route_tuple:
        for item in iter_archive_manifest_items(
            route.source,
            run_started_at_utc=run_started,
            versioning_state=_route_versioning_state(route),
            route_name=route.name,
            parser_kind=route.parser_kind,
            copy_mode=route.copy_mode,
            source_path=route.source_path,
            destination=route.destination,
            destination_path=route.destination_path,
            source_identity=route.source_identity,
            destination_identity=route.destination_identity,
        ):
            if store is None and len(entries) + len(skipped) >= _SQLITE_MANIFEST_ENTRY_THRESHOLD:
                store = SQLiteManifestStore.temporary()
                for entry in entries:
                    store.add_entry(entry)
                for skipped_object in skipped:
                    store.add_skipped(skipped_object)
                entries.clear()
                skipped.clear()
            if isinstance(item, ManifestEntry):
                if store is None:
                    entries.append(item)
                else:
                    store.add_entry(item)
            elif store is None:
                skipped.append(item)
            else:
                store.add_skipped(item)
    if store is not None:
        store.commit()
        store.assert_no_duplicate_sources()
        store.assert_no_duplicate_destinations()
        return ArchiveManifest(
            run_started,
            store.entries,
            None,
            store.archive_groups,
            store.skipped_objects,
            "sqlite",
            store.entry_size_sum(),
        )
    entry_tuple = tuple(entries)
    _reject_duplicate_sources(entry_tuple)
    grouped = archive_groups(entry_tuple)
    _reject_duplicate_destinations(entry_tuple, grouped)
    return ArchiveManifest(
        run_started,
        entry_tuple,
        None,
        grouped,
        tuple(skipped),
        source_byte_count=sum(entry.size for entry in entry_tuple),
    )


def _reject_overlapping_source_paths(routes: tuple[ArchiveManifestRouteSpec, ...]) -> None:
    seen: dict[str, list[tuple[str, str]]] = {}
    for route in routes:
        identity = repr(
            stable_identity_value(route.source_identity or storage_identity(route.source))
        )
        path = route.source_path
        for other_name, other_path in seen.setdefault(identity, []):
            if route_paths_overlap(path, other_path):
                message = (
                    f"overlapping source paths for storage location: {other_name!r} "
                    f"and {route.name!r}"
                )
                raise ValueError(message)
        seen[identity].append((route.name, path))


def _route_versioning_state(route: ArchiveManifestRouteSpec) -> VersioningState:
    return route.versioning_state or route.source.versioning_state()


def _reject_duplicate_sources(entries: tuple[ManifestEntry, ...]) -> None:
    _reject_duplicate_identities(
        (
            (entry.source_identity, entry.source_bucket, entry.key, entry.version_id)
            for entry in entries
        ),
        "duplicate source object identity",
    )


def _reject_duplicate_destinations(
    entries: Iterable[ManifestEntry], groups: Iterable[ArchiveGroup]
) -> None:
    _reject_duplicate_identities(
        chain(
            (
                (entry.destination_identity, entry.destination_bucket, entry.destination_key)
                for entry in entries
                if entry.copy_mode == "direct"
            ),
            (
                (
                    group.destination_identity,
                    group.destination_bucket,
                    group.destination_archive_key,
                )
                for group in groups
            ),
        ),
        "duplicate destination object identity",
    )


def _reject_duplicate_identities(keys: Iterable[object], message: str) -> None:
    seen: set[str] = set()
    for key in keys:
        stable_key = repr(stable_identity_value(key))
        if stable_key in seen:
            raise ValueError(message)
        seen.add(stable_key)
