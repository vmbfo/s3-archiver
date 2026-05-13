from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import cast

from s3_archiver_core._archive_manifest_models import ArchiveGroup, ManifestEntry


def direct_entries_for_route(
    entries: Sequence[ManifestEntry], route_name: str
) -> Iterable[ManifestEntry]:
    route_iter = getattr(entries, "iter_route", None)
    if callable(route_iter):
        typed_route_iter = cast(Callable[[str, str | None], Iterable[ManifestEntry]], route_iter)
        return typed_route_iter(route_name, "direct")
    return (
        entry for entry in entries if entry.route_name == route_name and entry.copy_mode == "direct"
    )


def direct_entry_count(entries: Sequence[ManifestEntry]) -> int:
    count_copy_mode = getattr(entries, "count_copy_mode", None)
    if callable(count_copy_mode):
        counted = count_copy_mode("direct")
        return counted if isinstance(counted, int) else 0
    return sum(1 for entry in entries if entry.copy_mode == "direct")


def archive_groups_for_route(
    groups: Sequence[ArchiveGroup], route_name: str
) -> Iterable[ArchiveGroup]:
    route_iter = getattr(groups, "iter_route", None)
    if callable(route_iter):
        typed_route_iter = cast(Callable[[str], Iterable[ArchiveGroup]], route_iter)
        return typed_route_iter(route_name)
    return (group for group in groups if group.route_name == route_name)
