"""Route archive manifest review edge coverage tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from s3_archiver_core.archive_manifest import (
    ArchiveManifestRoute,
    ParserKind,
    SelectedObject,
    SkippedObject,
    build_route_archive_manifest,
)
from s3_archiver_core.parsers.results import SkippedObject as ParserSkippedObject
from s3_archiver_core.s3 import S3ListedObject

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)


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
                parser_kind="direct",
                copy_mode="direct",
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
                parser_kind="direct",
                copy_mode="direct",
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
                parser_kind="direct",
                copy_mode="direct",
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
                    parser_kind="direct",
                    copy_mode="direct",
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
                    copy_mode="direct",
                ),
            ),
            run_started_at_utc=STARTED,
        )


@pytest.mark.unit()
@pytest.mark.parametrize(("left_path", "right_path"), (("data/", "data/fae/"), ("", "data/")))
def test_route_manifest_rejects_overlapping_source_paths(left_path: str, right_path: str) -> None:
    source, destination = FakeBucket("source"), FakeBucket("archive")

    with pytest.raises(ValueError, match="overlapping source paths"):
        _ = build_route_archive_manifest(
            (
                ArchiveManifestRoute(
                    "left",
                    source,
                    destination,
                    parser_kind="filename_timestamp",
                    copy_mode="daily_tar_gz",
                    source_path=left_path,
                ),
                ArchiveManifestRoute(
                    "right",
                    source,
                    destination,
                    parser_kind="filename_timestamp",
                    copy_mode="daily_tar_gz",
                    source_path=right_path,
                ),
            ),
            run_started_at_utc=STARTED,
        )


@pytest.mark.unit()
def test_route_manifest_allows_non_overlapping_source_paths_on_same_storage() -> None:
    source = FakeBucket("source")
    destination = FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "left",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
                source_path="left/",
            ),
            ArchiveManifestRoute(
                "right",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
                source_path="right/",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert manifest.entries == ()


@pytest.mark.unit()
@pytest.mark.parametrize(
    ("left_path", "right_path"),
    (("data", "database"), ("data/", "database/")),
)
def test_route_manifest_allows_sibling_source_paths_on_same_storage(
    left_path: str, right_path: str
) -> None:
    source, destination = FakeBucket("source"), FakeBucket("archive")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "data",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
                source_path=left_path,
            ),
            ArchiveManifestRoute(
                "database",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
                source_path=right_path,
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert manifest.entries == ()
