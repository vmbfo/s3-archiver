"""Tests for the visual demo CLI workflow."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, override

import pytest
import s3_archiver_cli.main as cli_module
import s3_archiver_cli.visual_demo as demo_module
import s3_archiver_cli.visual_demo_command as demo_command_module
import typer
from s3_archiver_core.archive import ArchiveRoute
from s3_archiver_core.s3 import S3ListedObject
from s3_archiver_core.settings import AppSettings, S3LocationSettings
from typer.testing import CliRunner

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed

RUNNER = CliRunner()


def _configure_logging(_: AppSettings) -> Path:
    return Path("/tmp/s3-archiver.log")


def _demo_settings(base_env: dict[str, str], tmp_path: Path) -> AppSettings:
    settings = AppSettings.from_env(base_env)
    return replace(settings, retention_days=60, temp_dir=tmp_path / "runtime-temp")


def _ok_archive_payload() -> dict[str, demo_module.JsonValue]:
    return {
        "status": "ok",
        "phases": {
            "list": {"status": "ok", "failure_count": 0},
            "copy": {"status": "ok", "failure_count": 0},
            "verify": {"status": "ok", "failure_count": 0},
        },
    }


class FakeReport:
    def as_dict(self) -> dict[str, demo_module.JsonValue]:
        return {"status": "ok", "checked_at": "2026-04-24T12:00:00+00:00"}


@pytest.mark.unit()
def test_demo_command_relays_visual_output_and_summary_json(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)

    def run_command(
        *,
        run_payload_command: object,
        archive_runner: object,
        emit: Callable[[str], None],
    ) -> None:
        _ = run_payload_command, archive_runner
        emit("== S3 Archiver Visual Demo ==")
        payload: dict[str, demo_module.JsonValue] = {"status": "ok"}
        emit(json.dumps(payload, sort_keys=True))

    monkeypatch.setattr(cli_module, "configure_logging", _configure_logging)
    monkeypatch.setattr(demo_command_module, "run", run_command)

    result = RUNNER.invoke(cli_module.app, ["demo"])

    assert result.exit_code == 0
    assert "== S3 Archiver Visual Demo ==" in result.stdout
    payload = cast(dict[str, object], json.loads(result.stdout.splitlines()[-1]))
    assert payload["status"] == "ok"


@pytest.mark.unit()
def test_demo_command_exits_non_zero_when_summary_reports_error(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    monkeypatch.setattr(cli_module, "configure_logging", _configure_logging)

    def run_command(
        *,
        run_payload_command: object,
        archive_runner: object,
        emit: Callable[[str], None],
    ) -> None:
        _ = run_payload_command, archive_runner, emit
        emit(json.dumps({"status": "error"}, sort_keys=True))
        raise typer.Exit(code=1)

    monkeypatch.setattr(demo_command_module, "run", run_command)

    result = RUNNER.invoke(cli_module.app, ["demo"])

    assert result.exit_code == 1


@pytest.mark.unit()
def test_run_visual_demo_reports_bucket_story(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
    tmp_path: Path,
) -> None:
    settings = _demo_settings(base_env, tmp_path)
    source = FakeBucket(
        settings.source.bucket,
        (
            _listed("demo/2024-02-20T00-00-00.txt", 61),
            _listed("demo/2024-04-21T00-00-00.txt", 1),
        ),
    )
    destination = SnapshotBucket(settings.destination.bucket)

    def archive_runner(_settings: AppSettings, _log_file: Path) -> dict[str, demo_module.JsonValue]:
        archived = _listed("demo/2024-02-20T00-00-00.txt", 61)
        metadata = {
            "s3-archiver-source-fingerprint": (
                '{"source_bucket":"source","source_key":"demo/2024-02-20T00-00-00.txt",'
                '"source_last_modified":"2024-02-19T00:00:00+00:00","source_size":10}'
            )
        }
        destination.objects = (
            replace(archived, properties=replace(archived.properties, metadata=metadata)),
        )
        return _ok_archive_payload()

    def fake_health_check(_settings: AppSettings, _log_file: Path) -> FakeReport:
        return FakeReport()

    def archive_routes(
        _settings: AppSettings,
        _build_client: Callable[[S3LocationSettings], object],
    ) -> tuple[ArchiveRoute, ...]:
        return (ArchiveRoute("default", source, destination),)

    lines: list[str] = []
    monkeypatch.setattr(demo_module, "run_health_check", fake_health_check)
    monkeypatch.setattr(demo_module, "archive_routes_from_settings", archive_routes)

    summary = demo_module.run_visual_demo(
        settings,
        settings.log_dir / "s3-archiver.log",
        archive_runner=archive_runner,
        emit=lines.append,
        now=lambda: datetime(2024, 4, 20, tzinfo=UTC),
    )

    assert summary["status"] == "ok"
    manifest = cast(dict[str, object], summary["archive_manifest"])
    assert manifest["object_count"] == 1
    snapshots = cast(dict[str, object], summary["snapshots"])
    before_archive = cast(dict[str, object], snapshots["before_archive"])
    after_archive = cast(dict[str, object], snapshots["after_archive"])
    assert before_archive["source_object_count"] == 2
    assert after_archive["destination_object_count"] == 1
    assert any("== Archive Candidates ==" in line for line in lines)
    assert any("source_last_modified=2024-02-19T00:00:00+00:00" in line for line in lines)
    assert not any("== Cleanup Preview ==" in line for line in lines)
    assert json.loads(lines[-1])["status"] == "ok"


@pytest.mark.unit()
def test_run_visual_demo_uses_default_utc_clock(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
    tmp_path: Path,
) -> None:
    settings = _demo_settings(base_env, tmp_path)

    class FrozenDateTime:
        @staticmethod
        def now(*, tz: object) -> datetime:
            _ = tz
            return datetime(2024, 4, 20, tzinfo=UTC)

    source = FakeBucket(settings.source.bucket, (_listed("demo/old.txt", 61),))
    destination = SnapshotBucket(settings.destination.bucket)

    def archive_runner(_settings: AppSettings, _log_file: Path) -> dict[str, demo_module.JsonValue]:
        return _ok_archive_payload()

    def fake_health_check(_settings: AppSettings, _log_file: Path) -> FakeReport:
        return FakeReport()

    def archive_routes(
        _settings: AppSettings,
        _build_client: Callable[[S3LocationSettings], object],
    ) -> tuple[ArchiveRoute, ...]:
        return (ArchiveRoute("default", source, destination),)

    monkeypatch.setattr(demo_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(demo_module, "run_health_check", fake_health_check)
    monkeypatch.setattr(demo_module, "archive_routes_from_settings", archive_routes)

    summary = demo_module.run_visual_demo(
        settings,
        settings.log_dir / "s3-archiver.log",
        archive_runner=archive_runner,
        emit=lambda _line: None,
    )

    assert summary["run_started_at_utc"] == "2024-04-20T00:00:00+00:00"


class SnapshotBucket(FakeBucket):
    """Archive bucket with mutable list output for visual demo snapshots."""

    objects: tuple[S3ListedObject, ...]

    def __init__(self, bucket: str, objects: tuple[S3ListedObject, ...] = ()) -> None:
        super().__init__(bucket)
        self.objects = objects

    @override
    def list_source_objects(self, versioning_state: str) -> tuple[S3ListedObject, ...]:
        assert versioning_state == self.versioning_state()
        return self.objects
