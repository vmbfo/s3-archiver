"""Archive copy success and verification edge coverage."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import override

import pytest
from s3_archiver_core._archive_copy import copy_direct_entry, copy_group, copy_phase
from s3_archiver_core.archive import ArchiveRoute
from s3_archiver_core.archive_group_metadata import ARCHIVE_SHA256_METADATA_KEY, group_metadata
from s3_archiver_core.archive_manifest import ArchiveManifest, ManifestEntry, build_archive_manifest

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed
from tests.unit.archive_workflow_fakes import object_properties as _properties


@pytest.mark.unit()
def test_copy_phase_collects_successful_direct_and_group_results() -> None:
    source, destination, direct_entry = _direct_manifest_objects()
    daily_source = FakeBucket("daily", (_listed("data/fae/2026-04-13T00-00-00Z.txt", 1),))
    daily_manifest = build_archive_manifest(
        daily_source,
        run_started_at_utc=datetime(2026, 4, 27, 12, tzinfo=UTC),
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
        destination=destination,
        route_name="daily",
    )
    manifest = ArchiveManifest(
        datetime(2026, 4, 27, 12, tzinfo=UTC),
        (direct_entry, *daily_manifest.entries),
        None,
        daily_manifest.archive_groups,
        (),
    )

    phase, groups, entries = copy_phase(
        manifest,
        {
            "default": ArchiveRoute(
                "default", source, destination, parser_kind="direct", copy_mode="direct"
            ),
            "daily": ArchiveRoute(
                "daily",
                daily_source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
            ),
        },
        None,
        lambda: False,
        lambda: 1.0,
    )

    assert phase.ok is True
    assert len(groups) == 1
    assert len(entries) == 1


@pytest.mark.unit()
def test_copy_direct_entry_reports_missing_source_before_copy() -> None:
    _source, destination, entry = _direct_manifest_objects()
    missing_source = FakeBucket("source")

    failure, copied = copy_direct_entry(
        ArchiveRoute(
            "default", missing_source, destination, parser_kind="direct", copy_mode="direct"
        ),
        entry,
        None,
    )

    assert failure == "data/raw.txt: data/raw.txt: listed source object disappeared before copy"
    assert copied is False


@pytest.mark.unit()
def test_copy_group_existing_archive_without_progress_logger_returns_verified() -> None:
    source = FakeBucket("source", (_listed("data/fae/2026-04-13T00-00-00Z.txt", 1),))
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=datetime(2026, 4, 27, 12, tzinfo=UTC),
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
    )
    group = manifest.archive_groups[0]
    metadata = dict(group_metadata(group))
    metadata[ARCHIVE_SHA256_METADATA_KEY] = "sha"
    destination = FakeBucket(
        "archive",
        destination={group.destination_archive_key: _properties(metadata=metadata)},
    )

    assert copy_group(source, destination, group, None) == (None, True)


@pytest.mark.unit()
def test_copy_group_reports_failed_upload_verification() -> None:
    source = FakeBucket("source", (_listed("data/fae/2026-04-13T00-00-00Z.txt", 1),))
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=datetime(2026, 4, 27, 12, tzinfo=UTC),
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
    )
    destination = _CorruptArchiveMetadataBucket("archive")

    failure, copied = copy_group(source, destination, manifest.archive_groups[0], None)

    assert failure == "data/fae/2026-04-13.tar.gz: archive verification failed"
    assert copied is False


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


class _CorruptArchiveMetadataBucket(FakeBucket):
    @override
    def upload_archive_file(
        self, destination_key: str, archive_path: Path, metadata: Mapping[str, str]
    ) -> None:
        super().upload_archive_file(destination_key, archive_path, metadata)
        self._destination[destination_key] = _properties(metadata={})
