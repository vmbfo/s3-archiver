"""Tests for daily archive CLI reporting payloads."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import s3_archiver_cli.error_logging as error_logging
import s3_archiver_cli.visual_demo as demo_module
import s3_archiver_cli.visual_demo_output as visual_demo_output
from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult
from s3_archiver_core.archive_manifest import ArchiveManifest
from s3_archiver_core.settings import AppSettings


@pytest.mark.unit()
def test_archive_result_payload_reports_daily_archive_groups(
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)
    entry = SimpleNamespace(
        key="data/fae/2026/04/13/07/2026-04-13T07-00-00.xml",
        version_id="v1",
        size=123,
        route_name="fae",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
        source_bucket="source-bucket",
        destination_bucket="destination-bucket",
        destination_archive_key="data/fae/2026-04-13.tar.gz",
    )
    skipped = SimpleNamespace(
        key="data/fae/no-timestamp.xml",
        reason="no timestamp in key",
        route_name="fae",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
        target_day="2026-04-13",
        archive_root="data/fae",
    )
    group = SimpleNamespace(
        target_day=date(2026, 4, 13),
        archive_root="data/fae",
        destination_archive_key="data/fae/2026-04-13.tar.gz",
        entries=(entry,),
        route_name="fae",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
        source_bucket="source-bucket",
        destination_bucket="destination-bucket",
    )
    manifest = SimpleNamespace(
        run_started_at_utc=datetime(2026, 4, 27, 2, tzinfo=UTC),
        target_day=date(2026, 4, 13),
        entries=(entry,),
        archive_groups=(group,),
        skipped_objects=(skipped,),
    )
    result = cast(
        ArchiveRunResult,
        cast(
            object,
            SimpleNamespace(
                run_id="run-id",
                manifest=manifest,
                list=ArchivePhaseResult("list"),
                copy=ArchivePhaseResult("copy"),
                verify=ArchivePhaseResult("verify"),
            ),
        ),
    )

    payload = error_logging.archive_result_payload("ok", result, settings, Path("/tmp/log"))

    assert payload["target_day"] == "2026-04-13"
    assert payload["archive_count"] == 1
    assert payload["source_object_count"] == 1
    assert payload["skipped_object_count"] == 1
    assert payload["destination_archive_keys"] == ["data/fae/2026-04-13.tar.gz"]
    groups = cast(list[dict[str, object]], payload["archive_groups"])
    assert "cleanup_status" not in groups[0]
    assert groups[0]["source_object_count"] == 1
    assert groups[0]["skipped_object_count"] == 0
    assert groups[0]["route_name"] == "fae"
    assert groups[0]["parser_kind"] == "filename_timestamp"
    assert groups[0]["copy_mode"] == "daily_tar_gz"
    source_objects = cast(list[dict[str, object]], groups[0]["source_objects"])
    assert source_objects[0]["route_name"] == "fae"
    manifest_payload = cast(dict[str, object], payload["manifest"])
    skipped_objects = cast(list[dict[str, object]], manifest_payload["skipped_objects"])
    assert skipped_objects[0]["route_name"] == "fae"
    assert cast(list[dict[str, object]], payload["routes"])[0]["copy_mode"] == "daily_tar_gz"


@pytest.mark.unit()
def test_archive_result_payload_uses_group_identity_fields(
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)
    entry = SimpleNamespace(
        key="data/fae/2026/04/13/07/2026-04-13T07-00-00.xml",
        version_id="v1",
        size=123,
        destination_archive_key="data/fae/2026-04-13.tar.gz",
    )
    group = SimpleNamespace(
        route_name="explicit-route",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
        source_identity="oci|https://source.example.test|eu-frankfurt-1|ns|explicit-source",
        source_bucket="explicit-source",
        destination_identity="oci|https://destination.example.test|eu-frankfurt-1|ns|explicit-dest",
        destination_bucket="explicit-dest",
        target_day=date(2026, 4, 13),
        archive_root="data/fae",
        destination_archive_key="data/fae/2026-04-13.tar.gz",
        entries=(entry,),
        skipped_objects=(),
    )
    manifest = SimpleNamespace(
        run_started_at_utc=datetime(2026, 4, 27, 2, tzinfo=UTC),
        target_day=date(2026, 4, 13),
        entries=(entry,),
        archive_groups=(group,),
        skipped_objects=(),
    )
    result = cast(
        ArchiveRunResult,
        cast(
            object,
            SimpleNamespace(
                run_id="run-id",
                manifest=manifest,
                list=ArchivePhaseResult("list"),
                copy=ArchivePhaseResult("copy"),
                verify=ArchivePhaseResult("verify"),
            ),
        ),
    )

    payload = error_logging.archive_result_payload("ok", result, settings, Path("/tmp/log"))

    groups = cast(list[dict[str, object]], payload["archive_groups"])
    assert groups[0]["route_name"] == "explicit-route"
    assert groups[0]["parser_kind"] == "filename_timestamp"
    assert groups[0]["copy_mode"] == "daily_tar_gz"
    assert groups[0]["source_identity"] == (
        "oci|https://source.example.test|eu-frankfurt-1|ns|explicit-source"
    )
    assert groups[0]["source_bucket"] == "explicit-source"
    assert groups[0]["destination_identity"] == (
        "oci|https://destination.example.test|eu-frankfurt-1|ns|explicit-dest"
    )
    assert groups[0]["destination_bucket"] == "explicit-dest"


@pytest.mark.unit()
def test_archive_result_payload_reports_direct_entries_as_destinations(
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)
    entry = SimpleNamespace(
        key="raw/live.txt",
        version_id="v1",
        size=123,
        route_name="raw",
        parser_kind="direct",
        copy_mode="direct",
        source_bucket="source-bucket",
        destination_bucket="destination-bucket",
        destination_key="raw/live.txt",
        destination_archive_key="raw/live.txt",
    )
    manifest = SimpleNamespace(
        run_started_at_utc=datetime(2026, 4, 27, 2, tzinfo=UTC),
        target_day=None,
        entries=(entry,),
        archive_groups=(),
        skipped_objects=(),
    )
    result = cast(
        ArchiveRunResult,
        cast(
            object,
            SimpleNamespace(
                run_id="run-id",
                manifest=manifest,
                list=ArchivePhaseResult("list"),
                copy=ArchivePhaseResult("copy"),
                verify=ArchivePhaseResult("verify"),
            ),
        ),
    )

    payload = error_logging.archive_result_payload("ok", result, settings, Path("/tmp/log"))
    direct_entries = cast(list[dict[str, object]], payload["direct_entries"])

    assert payload["archive_count"] == 0
    assert payload["direct_copy_count"] == 1
    assert payload["destination_archive_keys"] == []
    assert payload["destination_keys"] == ["raw/live.txt"]
    assert direct_entries[0]["copy_mode"] == "direct"
    assert direct_entries[0]["destination_key"] == "raw/live.txt"
    source_objects = cast(list[dict[str, object]], direct_entries[0]["source_objects"])
    assert source_objects[0]["destination_key"] == "raw/live.txt"


@pytest.mark.unit()
def test_visual_demo_describes_daily_candidates_and_result_groups() -> None:
    entry = SimpleNamespace(
        key="data/fae/2026-04-13T07-00-00.xml",
        version_id="v1",
        size=123,
        last_modified=datetime(2026, 4, 13, 7, tzinfo=UTC),
        etag='"etag"',
        source_bucket="source-bucket",
        destination_archive_key="data/fae/2026-04-13.tar.gz",
    )
    manifest = SimpleNamespace(
        run_started_at_utc=datetime(2026, 4, 27, 2, tzinfo=UTC),
        target_day="2026-04-13",
        entries=(entry,),
        skipped_objects=(SimpleNamespace(key="bad.txt", reason="no timestamp"),),
    )
    archive_payload: dict[str, demo_module.JsonValue] = {
        "status": "ok",
        "target_day": "2026-04-13",
        "archive_count": 1,
        "archive_groups": [
            {
                "destination_archive_key": "data/fae/2026-04-13.tar.gz",
                "source_object_count": 1,
                "skipped_object_count": 1,
            }
        ],
        "phases": {
            "list": {"status": "ok", "failure_count": 0},
            "copy": {"status": "ok", "failure_count": 0},
            "verify": {"status": "ok", "failure_count": 0},
        },
    }
    lines: list[str] = []

    visual_demo_output.emit_manifest(lines.append, cast(ArchiveManifest, cast(object, manifest)))
    visual_demo_output.emit_archive_result(lines.append, archive_payload)

    assert any(line == "target day: 2026-04-13" for line in lines)
    assert any("destination_archive_key=data/fae/2026-04-13.tar.gz" in line for line in lines)
    assert not any("cleanup_status=" in line for line in lines)
    assert any("SKIP   key=bad.txt reason=no timestamp" in line for line in lines)
