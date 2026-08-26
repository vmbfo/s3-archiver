"""Regression coverage for production key layouts reported as unarchived."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from s3_archiver_core.archive_manifest import (
    ArchiveManifestRoute,
    build_archive_manifest,
    build_route_archive_manifest,
)

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

RUN_STARTED = datetime(2026, 8, 26, tzinfo=UTC)


@pytest.mark.unit()
def test_harmonie_route_selects_flat_and_processor_filename_timestamps() -> None:
    source = FakeBucket(
        "source",
        (
            _listed(
                "data/harmonie/HARMONIE_DINI_SF_2026-05-20T210000Z_2026-05-20T210000Z.bz2",
                1,
            ),
            _listed("data/harmonie/processor/2026-05-20T220000Z.grib", 1),
        ),
    )

    manifest = build_archive_manifest(
        source,
        run_started_at_utc=RUN_STARTED,
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
        source_path="data/harmonie/",
        destination=FakeBucket("destination"),
        destination_path="data/harmonie/",
    )

    assert [
        (group.archive_root, group.destination_archive_key) for group in manifest.archive_groups
    ] == [
        ("", "data/harmonie/2026-05-20.tar.gz"),
        ("processor", "data/harmonie/processor/2026-05-20.tar.gz"),
    ]


@pytest.mark.unit()
def test_route_manifest_groups_nested_web_images_by_run_hour_and_domain() -> None:
    keys = (
        "data/wrf/web_img/2026/03/05/12/d01/clouds/Clouds - 2026-03-05 13:00:00.png",
        "data/wrf/web_img/2026/03/05/12/d01/overview/Overview - 2026-03-05 13:00:00.png",
        "data/wrf/web_img/2026/03/05/12/d02/wind/Wind - 2026-03-05 13:00:00.png",
    )
    source = FakeBucket(
        "folder-source",
        tuple(_listed(key, 1, f"v{index}") for index, key in enumerate(keys, start=1)),
    )

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "wrf-web-img",
                source,
                FakeBucket("archive"),
                source_path="data/wrf/web_img/",
                destination_path="data/wrf/web_img/",
                parser_kind="folder_timestamp_child",
                copy_mode="timestamp_child_tar_gz",
            ),
        ),
        run_started_at_utc=RUN_STARTED,
    )

    assert [
        (
            group.archive_root,
            group.destination_archive_key,
            {entry.key for entry in group.entries},
        )
        for group in manifest.archive_groups
    ] == [
        (
            "2026/03/05/12/d01",
            "data/wrf/web_img/2026-03-05-12-d01.tar.gz",
            {keys[0], keys[1]},
        ),
        (
            "2026/03/05/12/d02",
            "data/wrf/web_img/2026-03-05-12-d02.tar.gz",
            {keys[2]},
        ),
    ]
