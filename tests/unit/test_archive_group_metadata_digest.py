"""Unit coverage for the manifest-digest fallback in group metadata."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from s3_archiver_core._archive_manifest_digest import manifest_entries_sha256
from s3_archiver_core.archive_group_metadata import MANIFEST_SHA256_METADATA_KEY, group_metadata
from s3_archiver_core.archive_manifest import ArchiveGroup, ManifestEntry

from tests.unit.archive_workflow_fakes import listed_object as _listed


def _entry(key: str) -> ManifestEntry:
    listed = _listed(key, 1)
    return ManifestEntry(
        "source",
        key,
        listed.size,
        datetime(2024, 1, 1, tzinfo=UTC),
        listed.etag,
        listed.version_id,
        listed,
    )


@pytest.mark.unit()
def test_group_metadata_falls_back_to_entry_digest_when_manifest_sha256_missing() -> None:
    entries = (_entry("data/a.txt"), _entry("data/b.txt"))
    group = ArchiveGroup(
        date(2024, 1, 21),
        "",
        "2024-01-21.tar.gz",
        entries,
        manifest_sha256=None,
    )

    metadata = group_metadata(group)

    assert metadata[MANIFEST_SHA256_METADATA_KEY] == manifest_entries_sha256(entries)
    assert metadata[MANIFEST_SHA256_METADATA_KEY] != manifest_entries_sha256(())


@pytest.mark.unit()
def test_group_metadata_prefers_precomputed_manifest_sha256() -> None:
    group = ArchiveGroup(
        date(2024, 1, 21),
        "",
        "2024-01-21.tar.gz",
        (_entry("data/a.txt"),),
        manifest_sha256="precomputed-digest",
    )

    assert group_metadata(group)[MANIFEST_SHA256_METADATA_KEY] == "precomputed-digest"
