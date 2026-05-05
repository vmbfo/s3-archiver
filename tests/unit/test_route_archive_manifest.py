"""Route-aware archive manifest tests."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast, override

import pytest
from s3_archiver_core.archive_manifest import (
    ArchiveManifestRoute,
    ParserKind,
    SelectedObject,
    SkippedObject,
    build_route_archive_manifest,
)
from s3_archiver_core.parsers.results import SkippedObject as ParserSkippedObject
from s3_archiver_core.s3 import S3ListedObject, VersioningState

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)


class DuplicateListingBucket(FakeBucket):
    @override
    def list_source_objects(self, versioning_state: VersioningState) -> Iterable[S3ListedObject]:
        listed = tuple(super().list_source_objects(versioning_state))
        return (*listed, *listed)


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
        ("data/no-key-timestamp.txt", "parser timestamp after run start", "custom")
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
def test_route_manifest_accepts_parser_protocol_skip_result() -> None:
    source = FakeBucket("source", (_listed("data/file.txt", 1, None),))

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "custom",
                source,
                FakeBucket("archive"),
                parser=lambda _listed: ParserSkippedObject("parser protocol skip"),
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert [(item.key, item.reason, item.route_name) for item in manifest.skipped_objects] == [
        ("data/file.txt", "parser protocol skip", "custom")
    ]


@pytest.mark.unit()
def test_route_manifest_converts_parser_exceptions_to_object_skips() -> None:
    source = FakeBucket(
        "source",
        (
            _listed("data/good.txt", 1, None),
            _listed("data/bad.txt", 1, None),
        ),
    )

    def parser(listed: S3ListedObject) -> SelectedObject:
        if listed.key == "data/bad.txt":
            raise ValueError("bad parser input")
        return SelectedObject(datetime(2026, 4, 13, tzinfo=UTC), "last_modified")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "custom",
                source,
                FakeBucket("archive"),
                parser=parser,
                parser_kind="direct",
                copy_mode="direct",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert [entry.key for entry in manifest.entries] == ["data/good.txt"]
    assert [
        (item.key, item.reason, item.route_name, item.parser_kind, item.copy_mode)
        for item in manifest.skipped_objects
    ] == [("data/bad.txt", "parser error: bad parser input", "custom", "direct", "direct")]


@pytest.mark.unit()
def test_route_manifest_does_not_convert_infrastructure_parser_errors_to_skips() -> None:
    source = FakeBucket("source", (_listed("data/bad.txt", 1, None),))

    def parser(_listed: object) -> SelectedObject:
        raise RuntimeError("metadata lookup failed")

    with pytest.raises(RuntimeError, match="metadata lookup failed"):
        _ = build_route_archive_manifest(
            (
                ArchiveManifestRoute(
                    "custom",
                    source,
                    FakeBucket("archive"),
                    parser=parser,
                ),
            ),
            run_started_at_utc=STARTED,
        )


@pytest.mark.unit()
def test_route_manifest_does_not_convert_invalid_parser_kind_to_skip() -> None:
    source = FakeBucket("source", (_listed("data/bad.txt", 1, None),))

    with pytest.raises(ValueError, match="unsupported"):
        _ = build_route_archive_manifest(
            (
                ArchiveManifestRoute(
                    "custom",
                    source,
                    FakeBucket("archive"),
                    parser_kind=cast(ParserKind, cast(object, "unsupported")),
                ),
            ),
            run_started_at_utc=STARTED,
        )


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
def test_route_manifest_allows_non_overlapping_source_paths_on_same_storage() -> None:
    source = FakeBucket("source")
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute("left", source, destination, source_path="left/"),
            ArchiveManifestRoute("right", source, destination, source_path="right/"),
        ),
        run_started_at_utc=STARTED,
    )

    assert manifest.entries == ()


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


@pytest.mark.unit()
def test_route_manifest_rejects_duplicate_source_identities() -> None:
    source = DuplicateListingBucket("source", (_listed("same.txt", 1, "v1"),))

    with pytest.raises(ValueError, match="duplicate source object identity"):
        _ = build_route_archive_manifest(
            (
                ArchiveManifestRoute(
                    "duplicates",
                    source,
                    FakeBucket("destination"),
                    parser_kind="direct",
                    copy_mode="direct",
                ),
            ),
            run_started_at_utc=STARTED,
        )
