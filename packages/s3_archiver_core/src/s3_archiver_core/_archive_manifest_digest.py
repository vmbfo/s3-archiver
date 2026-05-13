from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import final

from s3_archiver_core._archive_identity import stable_identity_value
from s3_archiver_core._archive_manifest_models import ManifestEntry


def manifest_entries_sha256(entries: Iterable[ManifestEntry]) -> str:
    builder = ManifestDigestBuilder()
    for entry in entries:
        builder.add(entry)
    return builder.hexdigest()


@final
class ManifestDigestBuilder:
    def __init__(self) -> None:
        self._digest = hashlib.sha256()
        self._digest.update(b"[")
        self._first = True

    def add(self, entry: ManifestEntry) -> None:
        if self._first:
            self._first = False
        else:
            self._digest.update(b",")
        self._digest.update(
            json.dumps(_digest_row(entry), sort_keys=True, separators=(",", ":")).encode()
        )

    def hexdigest(self) -> str:
        digest = self._digest.copy()
        digest.update(b"]")
        return digest.hexdigest()


def _digest_row(entry: ManifestEntry) -> dict[str, object]:
    return {
        "copy_mode": entry.copy_mode,
        "destination_archive_key": entry.destination_archive_key,
        "key": entry.key,
        "parser_kind": entry.parser_kind,
        "route_name": entry.route_name,
        "size": entry.size,
        "source_bucket": entry.source_bucket,
        "source_identity": stable_identity_value(entry.source_identity),
        "source_path": entry.source_path,
        "etag": entry.etag,
        "version_id": entry.version_id,
        "selected_timestamp": (
            entry.selected_timestamp.isoformat() if entry.selected_timestamp else None
        ),
        "timestamp_source": entry.timestamp_source,
    }
