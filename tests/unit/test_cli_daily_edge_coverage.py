"""Focused coverage tests for daily archive CLI payload edge cases."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from s3_archiver_cli import archive_payloads, error_logging
from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult
from s3_archiver_core.settings import AppSettings


@pytest.mark.unit()
def test_archive_payload_helpers_cover_legacy_and_empty_fallbacks() -> None:
    entries = (
        SimpleNamespace(
            key="data/fae/old.xml",
            archive_root="data/fae",
            destination_archive_key="data/fae/2026-04-13.tar.gz",
        ),
    )
    manifest = SimpleNamespace(
        retention_cutoff_utc=None,
        entries=entries,
    )
    group = SimpleNamespace(
        target_day=datetime(2026, 4, 13, 7, tzinfo=UTC),
        destination_archive_keys=("data/fae/2026-04-13.tar.gz",),
        entries=(),
        skipped_objects=(),
    )

    assert archive_payloads.manifest_target_day(SimpleNamespace()) == ""
    assert archive_payloads.skipped_object_payloads(SimpleNamespace()) == []
    assert archive_payloads.object_list(object()) == []
    assert archive_payloads.archive_group_payload(group, manifest)["target_day"] == "2026-04-13"
    assert archive_payloads.archive_group_payload(group, manifest)["destination_archive_key"] == (
        "data/fae/2026-04-13.tar.gz"
    )
    assert (
        archive_payloads.archive_group_payload(
            SimpleNamespace(destination_archive_keys=(None,), entries=()),
            manifest,
        )["destination_archive_key"]
        == ""
    )

    payloads = archive_payloads.archive_group_payloads(manifest)

    assert payloads[0]["archive_root"] == "data/fae"

    fallback = archive_payloads.archive_group_payload(
        SimpleNamespace(entries=entries),
        manifest,
    )
    assert fallback["destination_archive_key"] == "data/fae/2026-04-13.tar.gz"


@pytest.mark.unit()
def test_archive_result_payload_omits_cleanup_group_status(
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)
    groups = (
        _group("data/skipped/2026-04-13.tar.gz"),
        _group("data/verified/2026-04-13.tar.gz"),
        _group("data/other/2026-04-13.tar.gz"),
    )
    manifest = SimpleNamespace(
        run_started_at_utc=datetime(2026, 4, 27, 2, tzinfo=UTC),
        retention_cutoff_utc=datetime(2026, 4, 13, 2, tzinfo=UTC),
        target_day=date(2026, 4, 13),
        entries=(),
        archive_groups=groups,
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
    payload_groups = cast(list[dict[str, object]], payload["archive_groups"])

    assert all("cleanup_status" not in group for group in payload_groups)


@pytest.mark.unit()
def test_archive_result_payload_omits_retention_cutoff_for_route_manifest(
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)
    manifest = SimpleNamespace(
        run_started_at_utc=datetime(2026, 4, 27, 2, tzinfo=UTC),
        retention_cutoff_utc=datetime(2026, 4, 27, 2, tzinfo=UTC),
        target_day=None,
        entries=(),
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
    manifest_payload = cast(dict[str, object], payload["manifest"])

    assert payload["target_day"] == ""
    assert "retention_cutoff_utc" not in manifest_payload


def _group(destination_archive_key: str) -> SimpleNamespace:
    return SimpleNamespace(
        target_day=date(2026, 4, 13),
        archive_root="data",
        destination_archive_key=destination_archive_key,
        entries=(),
        skipped_objects=(),
    )
