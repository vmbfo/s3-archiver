"""Route archive manifest duplicate identity edge coverage tests."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import override

import pytest
from s3_archiver_core.archive_manifest import ArchiveManifestRoute, build_route_archive_manifest
from s3_archiver_core.s3 import S3ListedObject, VersioningState

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)


class DuplicateListingBucket(FakeBucket):
    @override
    def list_source_objects(
        self, versioning_state: VersioningState, *, prefix: str = ""
    ) -> Iterable[S3ListedObject]:
        listed = tuple(super().list_source_objects(versioning_state, prefix=prefix))
        return (*listed, *listed)


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
def test_route_manifest_rejects_duplicate_daily_archive_destinations_across_routes() -> None:
    destination = FakeBucket("archive")

    with pytest.raises(ValueError, match="duplicate destination object identity"):
        _ = build_route_archive_manifest(
            (
                ArchiveManifestRoute(
                    "left",
                    FakeBucket("left", (_listed("left/2026-04-13T01-00-00Z.xml", 1, None),)),
                    destination,
                    source_path="left/",
                    destination_path="archives/common/",
                    parser_kind="filename_timestamp",
                    copy_mode="daily_tar_gz",
                ),
                ArchiveManifestRoute(
                    "right",
                    FakeBucket("right", (_listed("right/2026-04-13T02-00-00Z.xml", 1, None),)),
                    destination,
                    source_path="right/",
                    destination_path="archives/common/",
                    parser_kind="filename_timestamp",
                    copy_mode="daily_tar_gz",
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
