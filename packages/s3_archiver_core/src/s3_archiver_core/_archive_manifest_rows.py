from __future__ import annotations

from s3_archiver_core._archive_manifest_models import ManifestEntry
from s3_archiver_core._archive_manifest_sqlite import pack, stable_key


def entry_row(entry: ManifestEntry) -> tuple[object, ...]:
    return (
        entry.route_name,
        entry.copy_mode,
        stable_key(entry.source_identity),
        entry.source_bucket,
        entry.key,
        entry.version_id or "",
        stable_key(entry.destination_identity),
        entry.destination_bucket,
        entry.destination_key,
        entry.destination_archive_key,
        "" if entry.target_day is None else entry.target_day.isoformat(),
        entry.archive_root,
        entry.size,
        pack(entry),
    )
