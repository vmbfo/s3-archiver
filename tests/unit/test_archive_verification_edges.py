"""Additional archive verification edge cases."""

from __future__ import annotations

from dataclasses import replace

import pytest
from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.archive_transfer import verify_source_unchanged

from tests.unit.archive_workflow_fakes import listed_object as _listed
from tests.unit.archive_workflow_fakes import object_properties as _properties


@pytest.mark.unit()
def test_verify_source_unchanged_rejects_checksum_mismatch() -> None:
    listed = replace(
        _listed("old.txt", 90),
        properties=_properties(checksums={"sha256": "expected"}, checksum_type="FULL_OBJECT"),
    )
    entry = ManifestEntry("source", "old.txt", 10, listed.last_modified, '"etag"', "v1", listed)
    current = _properties(checksums={"sha256": "other"}, checksum_type="FULL_OBJECT")

    assert verify_source_unchanged(entry, current).ok is False
