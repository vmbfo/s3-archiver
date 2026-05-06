from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from s3_archiver_core._archive_manifest_builder import (
    archive_groups,
    build_archive_manifest,
)
from s3_archiver_core._archive_manifest_models import (
    ArchiveGroup,
    ArchiveManifest,
    ArchiveManifestRoute,
    ManifestEntry,
    SkippedObject,
)
from s3_archiver_core._archive_manifest_paths import (
    as_utc,
    normalize_prefix,
    storage_identity,
)


def build_route_archive_manifest(
    routes: Iterable[ArchiveManifestRoute],
    *,
    run_started_at_utc: datetime,
) -> ArchiveManifest:
    """Build a deterministic global manifest for route-based archiving."""

    run_started = as_utc(run_started_at_utc)
    route_tuple = tuple(routes)
    _reject_overlapping_source_paths(route_tuple)
    entries: list[ManifestEntry] = []
    skipped: list[SkippedObject] = []
    for route in route_tuple:
        manifest = build_archive_manifest(
            route.source,
            run_started_at_utc=run_started,
            versioning_state=(
                route.versioning_state
                if route.versioning_state is not None
                else route.source.versioning_state()
            ),
            route_name=route.name,
            parser_kind=route.parser_kind,
            copy_mode=route.copy_mode,
            source_path=route.source_path,
            destination=route.destination,
            destination_path=route.destination_path,
            parser=route.parser,
            source_identity=route.source_identity,
            destination_identity=route.destination_identity,
        )
        entries.extend(manifest.entries)
        skipped.extend(manifest.skipped_objects)
    entry_tuple = tuple(entries)
    _reject_duplicate_sources(entry_tuple)
    grouped = archive_groups(entry_tuple)
    _reject_duplicate_destinations(entry_tuple, grouped)
    return ArchiveManifest(run_started, entry_tuple, None, grouped, tuple(skipped))


def _reject_overlapping_source_paths(routes: tuple[ArchiveManifestRoute, ...]) -> None:
    seen: dict[object, list[tuple[str, str]]] = {}
    for route in routes:
        identity = route.source_identity or storage_identity(route.source)
        path = route.source_path
        for other_name, other_path in seen.setdefault(identity, []):
            if _prefixes_overlap(path, other_path):
                message = (
                    f"overlapping source paths for storage location: {other_name!r} "
                    f"and {route.name!r}"
                )
                raise ValueError(message)
        seen[identity].append((route.name, path))


def _reject_duplicate_sources(entries: tuple[ManifestEntry, ...]) -> None:
    _reject_duplicate_identities(
        (
            (entry.source_identity, entry.source_bucket, entry.key, entry.version_id)
            for entry in entries
        ),
        "duplicate source object identity",
    )


def _reject_duplicate_destinations(
    entries: tuple[ManifestEntry, ...], groups: tuple[ArchiveGroup, ...]
) -> None:
    _reject_duplicate_identities(
        (
            *(
                (entry.destination_identity, entry.destination_bucket, entry.destination_key)
                for entry in entries
                if entry.copy_mode == "direct"
            ),
            *(
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
    seen: set[object] = set()
    for key in keys:
        if key in seen:
            raise ValueError(message)
        seen.add(key)


def _prefixes_overlap(left: str, right: str) -> bool:
    left_prefix = _route_path_prefix(left)
    right_prefix = _route_path_prefix(right)
    return left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)


def _route_path_prefix(path: str) -> str:
    normalized = normalize_prefix(path).rstrip("/")
    if normalized == "":
        return ""
    return f"{normalized}/"
