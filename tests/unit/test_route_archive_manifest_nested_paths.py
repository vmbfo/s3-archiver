"""Nested source-path longest-prefix exclusion routing tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from s3_archiver_core.archive_manifest import ArchiveManifestRoute, build_route_archive_manifest

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)


@pytest.mark.unit()
def test_nested_child_claims_subtree_parent_excludes_it() -> None:
    source = FakeBucket(
        "source",
        (
            _listed("data/harmonie/a.txt", 1, None),
            _listed("data/harmonie/processor/b.txt", 1, None),
        ),
    )
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "parent",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/harmonie/",
            ),
            ArchiveManifestRoute(
                "child",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/harmonie/processor/",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    by_route = {(entry.key, entry.route_name) for entry in manifest.entries}
    assert ("data/harmonie/processor/b.txt", "child") in by_route
    assert ("data/harmonie/processor/b.txt", "parent") not in by_route
    assert ("data/harmonie/a.txt", "parent") in by_route
    assert ("data/harmonie/a.txt", "child") not in by_route
    assert len(manifest.entries) == 2


@pytest.mark.unit()
def test_excluded_objects_are_not_in_skipped_objects() -> None:
    source = FakeBucket(
        "source",
        (
            _listed("data/harmonie/a.txt", 1, None),
            _listed("data/harmonie/processor/b.txt", 1, None),
        ),
    )
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "parent",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/harmonie/",
            ),
            ArchiveManifestRoute(
                "child",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/harmonie/processor/",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    skipped_keys = {skipped.key for skipped in manifest.skipped_objects}
    assert "data/harmonie/processor/b.txt" not in skipped_keys
    assert len(manifest.skipped_objects) == 0


@pytest.mark.unit()
def test_three_level_nesting_assigns_deepest_route_only() -> None:
    source = FakeBucket(
        "source",
        (
            _listed("data/top.txt", 1, None),
            _listed("data/harmonie/mid.txt", 1, None),
            _listed("data/harmonie/processor/deep.txt", 1, None),
        ),
    )
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "top",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/",
            ),
            ArchiveManifestRoute(
                "mid",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/harmonie/",
            ),
            ArchiveManifestRoute(
                "deep",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/harmonie/processor/",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    route_by_key = {entry.key: entry.route_name for entry in manifest.entries}
    assert route_by_key == {
        "data/top.txt": "top",
        "data/harmonie/mid.txt": "mid",
        "data/harmonie/processor/deep.txt": "deep",
    }
    assert len(manifest.entries) == 3


@pytest.mark.unit()
def test_whole_bucket_parent_excludes_nested_child() -> None:
    source = FakeBucket(
        "source",
        (
            _listed("root.txt", 1, None),
            _listed("data/nested.txt", 1, None),
        ),
    )
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "whole",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="",
            ),
            ArchiveManifestRoute(
                "nested",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    route_by_key = {entry.key: entry.route_name for entry in manifest.entries}
    assert route_by_key == {"root.txt": "whole", "data/nested.txt": "nested"}
    assert len(manifest.entries) == 2


@pytest.mark.unit()
def test_overlapping_paths_on_different_storage_are_not_excluded() -> None:
    source_a = FakeBucket(
        "bucket-a",
        (
            _listed("data/harmonie/a.txt", 1, None),
            _listed("data/harmonie/processor/b.txt", 1, None),
        ),
    )
    source_b = FakeBucket("bucket-b", (_listed("data/harmonie/processor/c.txt", 1, None),))
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "route-a",
                source_a,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/harmonie/",
                destination_path="a/",
            ),
            ArchiveManifestRoute(
                "route-b",
                source_b,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                source_path="data/harmonie/processor/",
                destination_path="b/",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    keys_by_route: dict[str, set[str]] = {}
    for entry in manifest.entries:
        keys_by_route.setdefault(entry.route_name, set()).add(entry.key)
    assert keys_by_route["route-a"] == {
        "data/harmonie/a.txt",
        "data/harmonie/processor/b.txt",
    }
    assert keys_by_route["route-b"] == {"data/harmonie/processor/c.txt"}


@pytest.mark.unit()
def test_nested_routes_keep_per_route_config() -> None:
    source = FakeBucket(
        "source",
        (
            _listed("data/harmonie/2026-04-13T01-00-00Z.xml", 1, None),
            _listed("data/harmonie/processor/2026-04-13T02-00-00Z.xml", 1, None),
        ),
    )
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "parent",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
                source_path="data/harmonie/",
                destination_path="parent/",
            ),
            ArchiveManifestRoute(
                "child",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="timestamp_child_tar_gz",
                source_path="data/harmonie/processor/",
                destination_path="child/",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    config_by_route = {
        entry.route_name: (entry.parser_kind, entry.copy_mode) for entry in manifest.entries
    }
    assert config_by_route["parent"] == ("filename_timestamp", "daily_tar_gz")
    assert config_by_route["child"] == ("filename_timestamp", "timestamp_child_tar_gz")
    assert len(manifest.entries) == 2
