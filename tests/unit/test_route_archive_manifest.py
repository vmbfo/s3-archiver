"""Route-aware archive manifest tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from s3_archiver_core.archive_manifest import (
    ArchiveManifestRoute,
    build_route_archive_manifest,
)

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)


@pytest.mark.unit()
def test_route_manifest_builds_direct_and_daily_destinations() -> None:
    direct_source = FakeBucket("direct-source", (_listed("raw/a.txt", 1, None),))
    daily_source = FakeBucket(
        "daily-source",
        (_listed("data/fae/2026/04/13/2026-04-13T03-00-00Z.xml", 1, "v1"),),
    )
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "raw",
                direct_source,
                destination,
                source_path="raw/",
                destination_path="copy/",
                parser_kind="direct",
                copy_mode="direct",
            ),
            ArchiveManifestRoute(
                "fae",
                daily_source,
                destination,
                source_path="data/fae/",
                destination_path="archives/fae/",
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert [
        (entry.route_name, entry.copy_mode, entry.destination_key) for entry in manifest.entries
    ] == [
        ("raw", "direct", "copy/raw/a.txt"),
        ("fae", "daily_tar_gz", "archives/fae/2026-04-13.tar.gz"),
    ]
    assert manifest.archive_groups[0].destination_archive_key == "archives/fae/2026-04-13.tar.gz"


@pytest.mark.unit()
def test_route_manifest_uses_registered_folder_timestamp_parser() -> None:
    source = FakeBucket(
        "folder-source",
        (_listed("data/fae/2026/04/13/07/no-stamp.xml", 1, "v1"),),
    )

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "folder-route",
                source,
                FakeBucket("archive"),
                source_path="data/fae/",
                destination_path="copy/",
                parser_kind="folder_timestamp",
                copy_mode="direct",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert [
        (
            entry.route_name,
            entry.parser_kind,
            entry.copy_mode,
            entry.selected_timestamp,
            entry.timestamp_source,
            entry.archive_root,
            entry.destination_key,
        )
        for entry in manifest.entries
    ] == [
        (
            "folder-route",
            "folder_timestamp",
            "direct",
            datetime(2026, 4, 13, 7, tzinfo=UTC),
            "path",
            "",
            "copy/data/fae/2026/04/13/07/no-stamp.xml",
        )
    ]


@pytest.mark.unit()
def test_route_manifest_keeps_parser_selection_independent_from_copy_mode() -> None:
    filename_direct = FakeBucket(
        "filename-source",
        (_listed("data/fae/2026-04-13T03-00-00Z.xml", 1, "v1"),),
    )
    direct_daily = FakeBucket("direct-source", (_listed("raw/current.txt", 1, "v1"),))

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "filename-direct",
                filename_direct,
                FakeBucket("archive"),
                destination_path="copy/",
                parser_kind="filename_timestamp",
                copy_mode="direct",
            ),
            ArchiveManifestRoute(
                "direct-daily",
                direct_daily,
                FakeBucket("archive"),
                source_path="raw/",
                destination_path="archives/raw/",
                parser_kind="direct",
                copy_mode="daily_tar_gz",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert [
        (
            entry.route_name,
            entry.parser_kind,
            entry.copy_mode,
            entry.selected_timestamp,
            entry.timestamp_source,
            entry.destination_key,
        )
        for entry in manifest.entries
    ] == [
        (
            "filename-direct",
            "filename_timestamp",
            "direct",
            datetime(2026, 4, 13, 3, tzinfo=UTC),
            "basename",
            "copy/data/fae/2026-04-13T03-00-00Z.xml",
        ),
        (
            "direct-daily",
            "direct",
            "daily_tar_gz",
            datetime(2024, 4, 19, tzinfo=UTC),
            "last_modified",
            "archives/raw/2024-04-19.tar.gz",
        ),
    ]
    assert [
        (group.route_name, group.parser_kind, group.copy_mode, group.destination_archive_key)
        for group in manifest.archive_groups
    ] == [("direct-daily", "direct", "daily_tar_gz", "archives/raw/2024-04-19.tar.gz")]
