"""Route manifest compatibility tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import pytest
from s3_archiver_core.archive_manifest import build_route_archive_manifest
from s3_archiver_core.s3 import VersioningState

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)


@dataclass(frozen=True)
class _LegacyRouteSpec:
    name: str
    source: FakeBucket
    destination: FakeBucket
    parser_kind: str
    copy_mode: Literal["direct", "daily_tar_gz"]
    source_path: str = ""
    destination_path: str = ""
    versioning_state: VersioningState | None = None
    source_identity: object | None = None
    destination_identity: object | None = None


@pytest.mark.unit()
def test_route_manifest_accepts_legacy_route_specs_without_group_option() -> None:
    source = FakeBucket("daily-source", (_listed("data/fae/2026-04-13T03-00-00Z.xml", 1, "v1"),))

    manifest = build_route_archive_manifest(
        (
            _LegacyRouteSpec(
                "fae",
                source,
                FakeBucket("archive"),
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
                source_path="data/fae/",
                destination_path="archives/fae/",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert manifest.archive_groups[0].destination_archive_key == "archives/fae/2026-04-13.tar.gz"


@pytest.mark.unit()
def test_route_manifest_rejects_invalid_group_option_type() -> None:
    source = FakeBucket("daily-source", (_listed("data/fae/2026-04-13T03-00-00Z.xml", 1, "v1"),))
    route = _LegacyRouteSpec(
        "fae",
        source,
        FakeBucket("archive"),
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
    )
    object.__setattr__(route, "copy_mode_group_after_timestamp_parts", True)

    with pytest.raises(TypeError, match="copy_mode_group_after_timestamp_parts"):
        _ = build_route_archive_manifest((route,), run_started_at_utc=STARTED)
