"""Focused coverage tests for direct archive copy edge paths."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import override

import pytest
from s3_archiver_core._archive_copy import copy_direct_entry
from s3_archiver_core.archive import ArchiveRoute
from s3_archiver_core.archive_manifest import ManifestEntry, build_archive_manifest
from s3_archiver_core.archive_transfer import archive_metadata
from s3_archiver_core.s3 import S3ObjectProperties

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed


@pytest.mark.unit()
def test_direct_copy_existing_verified_destination_is_reused() -> None:
    source, destination, entry = _direct_manifest_objects()
    destination = FakeBucket(
        "archive",
        destination={
            entry.destination_key: replace(
                entry.object.properties, metadata=archive_metadata(entry)
            )
        },
    )

    failure, copied = copy_direct_entry(
        ArchiveRoute("direct", source, destination, parser_kind="direct", copy_mode="direct"),
        entry,
        None,
    )

    assert failure is None
    assert copied is True
    assert destination.copied == []


@pytest.mark.unit()
def test_direct_copy_reports_copy_and_post_copy_verification_failures() -> None:
    source, destination, entry = _direct_manifest_objects()
    destination.fail_copy = True

    failure, copied = copy_direct_entry(
        ArchiveRoute("direct", source, destination, parser_kind="direct", copy_mode="direct"),
        entry,
        None,
    )

    assert failure == "data/raw.txt: copy failed"
    assert copied is False

    failure, copied = copy_direct_entry(
        ArchiveRoute(
            "direct",
            source,
            MissingDirectDestinationBucket("archive"),
            parser_kind="direct",
            copy_mode="direct",
        ),
        entry,
        None,
    )

    assert failure == "data/raw.txt: destination missing"
    assert copied is False


class MissingDirectDestinationBucket(FakeBucket):
    @override
    def head_object(self, key: str, version_id: str | None = None) -> S3ObjectProperties | None:
        _ = key
        _ = version_id
        return None


def _direct_manifest_objects() -> tuple[FakeBucket, FakeBucket, ManifestEntry]:
    listed = _listed("data/raw.txt", 1, "v1")
    source = FakeBucket("source", (listed,))
    destination = FakeBucket("archive")
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=datetime(2026, 4, 27, 12, tzinfo=UTC),
        versioning_state="Enabled",
        destination=destination,
        parser_kind="direct",
        copy_mode="direct",
    )
    return source, destination, manifest.entries[0]
