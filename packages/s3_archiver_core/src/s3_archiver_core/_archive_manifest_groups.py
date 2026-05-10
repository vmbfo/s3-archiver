from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from s3_archiver_core._archive_manifest_models import ManifestEntry


def group_entries(
    entries: tuple[ManifestEntry, ...],
    route_name: str,
    root: str,
    target_day: date,
    destination_key: str,
) -> Iterable[ManifestEntry]:
    return (
        entry
        for entry in entries
        if entry.route_name == route_name
        and entry.archive_root == root
        and entry.target_day == target_day
        and entry.destination_archive_key == destination_key
    )
