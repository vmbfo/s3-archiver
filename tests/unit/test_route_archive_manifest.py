"""Route-aware archive manifest tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from s3_archiver_core.archive_manifest import (
    ArchiveManifestRoute,
    SelectedObject,
    SkippedObject,
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
def test_route_manifest_uses_parser_selected_timestamp_for_eligibility() -> None:
    source = FakeBucket("source", (_listed("data/no-key-timestamp.txt", 1, None),))
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "custom",
                source,
                destination,
                parser=lambda _listed: SelectedObject(
                    datetime(2026, 4, 28, tzinfo=UTC),
                    "last_modified",
                ),
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert manifest.entries == ()
    assert [(item.key, item.reason, item.route_name) for item in manifest.skipped_objects] == [
        ("data/no-key-timestamp.txt", "outside retention window", "custom")
    ]


@pytest.mark.unit()
def test_route_manifest_preserves_parser_skip_reasons() -> None:
    source = FakeBucket("source", (_listed("data/file.txt", 1, None),))

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "custom",
                source,
                FakeBucket("archive"),
                parser=lambda listed: SkippedObject(listed.key, "parser said no"),
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert [(item.key, item.reason, item.route_name) for item in manifest.skipped_objects] == [
        ("data/file.txt", "parser said no", "custom")
    ]


@pytest.mark.unit()
def test_route_manifest_rejects_overlapping_source_paths() -> None:
    source = FakeBucket("source")
    destination = FakeBucket("archive")

    with pytest.raises(ValueError, match="overlapping source paths"):
        _ = build_route_archive_manifest(
            (
                ArchiveManifestRoute("left", source, destination, source_path="data/"),
                ArchiveManifestRoute("right", source, destination, source_path="data/fae/"),
            ),
            run_started_at_utc=STARTED,
        )


@pytest.mark.unit()
def test_route_manifest_rejects_duplicate_destinations_across_routes() -> None:
    destination = FakeBucket("archive")

    with pytest.raises(ValueError, match="duplicate destination object identity"):
        _ = build_route_archive_manifest(
            (
                ArchiveManifestRoute(
                    "left",
                    FakeBucket("left", (_listed("same.txt", 1, None),)),
                    destination,
                    parser_kind="direct",
                    copy_mode="direct",
                ),
                ArchiveManifestRoute(
                    "right",
                    FakeBucket("right", (_listed("same.txt", 1, None),)),
                    destination,
                    parser_kind="direct",
                    copy_mode="direct",
                ),
            ),
            run_started_at_utc=STARTED,
        )
