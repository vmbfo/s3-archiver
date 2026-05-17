"""Route archive manifest grouping tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from s3_archiver_core.archive_manifest import ArchiveManifestRoute, build_route_archive_manifest

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed


@pytest.mark.unit()
def test_route_manifest_groups_after_folder_timestamp_segments() -> None:
    source = FakeBucket(
        "folder-source",
        (
            _listed("data/wrf/ecmwf/2026/05/16/00/d01/out-a.grib", 1, "v1"),
            _listed("data/wrf/ecmwf/2026/05/16/00/d02/out-b.grib", 1, "v2"),
            _listed("data/wrf/ecmwf/2026/05/16/06/d01/out-c.grib", 1, "v3"),
        ),
    )

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "wrf",
                source,
                FakeBucket("archive"),
                source_path="data/wrf/ecmwf/",
                destination_path="data/wrf/ecmwf/",
                parser_kind="folder_timestamp",
                copy_mode="daily_tar_gz",
                copy_mode_group_after_timestamp_parts=1,
            ),
        ),
        run_started_at_utc=datetime(2026, 5, 17, tzinfo=UTC),
    )

    assert [
        (group.archive_root, group.destination_archive_key) for group in manifest.archive_groups
    ] == [
        ("2026/05/16/00/d01", "data/wrf/ecmwf/2026/05/16/00/d01/2026-05-16.tar.gz"),
        ("2026/05/16/00/d02", "data/wrf/ecmwf/2026/05/16/00/d02/2026-05-16.tar.gz"),
        ("2026/05/16/06/d01", "data/wrf/ecmwf/2026/05/16/06/d01/2026-05-16.tar.gz"),
    ]


@pytest.mark.unit()
def test_route_manifest_groups_after_latest_folder_timestamp_segments() -> None:
    source = FakeBucket(
        "folder-source",
        (_listed("data/model/2026/05/15/run/2026/05/16/00/d01/out.grib", 1, "v1"),),
    )

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "model",
                source,
                FakeBucket("archive"),
                source_path="data/model/",
                destination_path="data/model/",
                parser_kind="folder_timestamp",
                copy_mode="daily_tar_gz",
                copy_mode_group_after_timestamp_parts=1,
            ),
        ),
        run_started_at_utc=datetime(2026, 5, 17, tzinfo=UTC),
    )

    assert [
        (group.archive_root, group.destination_archive_key, group.entries[0].selected_timestamp)
        for group in manifest.archive_groups
    ] == [
        (
            "2026/05/16/00/d01",
            "data/model/2026/05/16/00/d01/2026-05-16.tar.gz",
            datetime(2026, 5, 16, tzinfo=UTC),
        )
    ]


@pytest.mark.unit()
def test_route_manifest_ignores_folder_grouping_for_filename_timestamp_routes() -> None:
    source = FakeBucket(
        "filename-source",
        (_listed("data/model/run/2026/05/16/00/d01/out-2026-05-17T00-00-00Z.grib", 1, "v1"),),
    )

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "model",
                source,
                FakeBucket("archive"),
                source_path="data/model/",
                destination_path="data/model/",
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
                copy_mode_group_after_timestamp_parts=1,
            ),
        ),
        run_started_at_utc=datetime(2026, 5, 18, tzinfo=UTC),
    )

    assert [
        (group.archive_root, group.destination_archive_key) for group in manifest.archive_groups
    ] == [
        (
            "run/2026/05/16/00/d01",
            "data/model/run/2026/05/16/00/d01/2026-05-17.tar.gz",
        )
    ]
