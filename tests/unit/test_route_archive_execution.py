"""Route-aware archive execution tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from s3_archiver_core.archive import ArchiveRoute, run_archive, run_archive_routes
from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.archive_options import ArchiveOptions, ArchiveRouteOptions
from s3_archiver_core.archive_transfer import archive_metadata
from s3_archiver_core.s3 import S3TransferCapabilities

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)


@pytest.mark.unit()
def test_run_archive_direct_copy_mode_copies_and_verifies_without_cleanup() -> None:
    listed = _listed("data/raw.txt", 1, "v1")
    source = FakeBucket("source", (listed,))
    destination = FakeBucket("archive")

    result = run_archive(
        source,
        destination,
        ArchiveOptions(
            retention_days=14,
            cleanup_enabled=True,
            routes=(
                ArchiveRouteOptions(
                    "default",
                    destination_path="mirror/",
                    parser_kind="direct",
                    copy_mode="direct",
                ),
            ),
        ),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )

    assert result.ok is True
    assert destination.copied == ["data/raw.txt"]
    assert destination.head_object("mirror/data/raw.txt") is not None
    assert source.deleted == []
    assert result.cleanup.skipped is True


@pytest.mark.unit()
def test_direct_copy_uses_route_transfer_capabilities() -> None:
    listed = _listed("data/raw.txt", 1, "v1")
    source = FakeBucket("source", (listed,))
    destination = FakeBucket("archive")

    result = run_archive_routes(
        (
            ArchiveRoute(
                "direct",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
                transfer_capabilities=S3TransferCapabilities(
                    native_copy=False,
                    multipart_copy=False,
                    streaming_upload=True,
                    temp_file_backed=True,
                ),
            ),
        ),
        ArchiveOptions(retention_days=14),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )

    assert result.ok is True
    assert destination.copied == ["data/raw.txt"]
    assert destination.copy_strategies == ["multipart_streaming"]


@pytest.mark.unit()
def test_run_archive_routes_uses_one_worker_per_route() -> None:
    left = FakeBucket("left", (_listed("left/2026-04-13T00-00-00Z.txt", 1, None),))
    right = FakeBucket("right", (_listed("right.txt", 1, None),))
    destination = FakeBucket("archive")
    decisions: list[tuple[str, str]] = []

    result = run_archive_routes(
        (
            ArchiveRoute(
                "daily",
                left,
                destination,
                source_path="left/",
                destination_path="archives/",
            ),
            ArchiveRoute(
                "direct",
                right,
                destination,
                destination_path="mirror/",
                parser_kind="direct",
                copy_mode="direct",
            ),
        ),
        ArchiveOptions(retention_days=14, cleanup_enabled=True),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
        debug_logger=lambda entry, strategy: decisions.append((entry.route_name, strategy)),
    )

    assert result.ok is True
    assert sorted(decisions) == [
        ("daily", "deterministic_tar_gzip"),
        ("direct", "simple_native_copy"),
    ]
    assert destination.uploaded == ["archives/2026-04-13.tar.gz"]
    assert destination.copied == ["right.txt"]
    assert left.deleted == []
    assert right.deleted == []


@pytest.mark.unit()
def test_direct_copy_existing_conflicting_destination_fails() -> None:
    listed = _listed("data/raw.txt", 1, "v1")
    source = FakeBucket("source", (listed,))
    destination = FakeBucket(
        "archive",
        destination={
            "data/raw.txt": replace(
                listed.properties,
                metadata=archive_metadata(
                    ManifestEntry(
                        "source",
                        "other.txt",
                        listed.size,
                        listed.last_modified,
                        listed.etag,
                        listed.version_id,
                        replace(listed, key="other.txt"),
                    )
                ),
            )
        },
    )

    result = run_archive(
        source,
        destination,
        ArchiveOptions(
            retention_days=14,
            routes=(
                ArchiveRouteOptions(
                    "default",
                    parser_kind="direct",
                    copy_mode="direct",
                ),
            ),
        ),
        run_started_at_utc=STARTED,
        clock=lambda: STARTED,
    )

    assert result.copy.failures == ("data/raw.txt: source fingerprint mismatch",)
    assert result.verify.skipped is True
