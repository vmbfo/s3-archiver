"""Tests for the visual demo CLI workflow."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import s3_archiver_cli.main as cli_module
import s3_archiver_cli.visual_demo as demo_module
from s3_archiver_core.settings import AppSettings, S3LocationSettings
from typer.testing import CliRunner

from tests.unit.archive_workflow_fakes import listed_object as _listed

RUNNER = CliRunner()


def _configure_logging(_: AppSettings) -> Path:
    return Path("/tmp/s3-archiver.log")


def _fake_build_client(_location: S3LocationSettings) -> object:
    return object()


@pytest.mark.unit()
def test_demo_command_relays_visual_output_and_summary_json(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)

    def run_demo(
        _settings: AppSettings,
        _log_file: Path,
        *,
        archive_runner: object,
        cleanup_preview_runner: object,
        emit: Callable[[str], None],
    ) -> dict[str, demo_module.JsonValue]:
        _ = archive_runner
        _ = cleanup_preview_runner
        emit("== S3 Archiver Visual Demo ==")
        payload: dict[str, demo_module.JsonValue] = {
            "status": "ok",
            "cleanup_preview_left_bucket_state_unchanged": True,
        }
        emit(json.dumps(payload, sort_keys=True))
        return payload

    monkeypatch.setattr(cli_module, "configure_logging", _configure_logging)
    monkeypatch.setattr(cli_module, "_run_visual_demo", run_demo)

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

    def run_demo(
        _settings: AppSettings,
        _log_file: Path,
        *,
        archive_runner: object,
        cleanup_preview_runner: object,
        emit: Callable[[str], None],
    ) -> dict[str, demo_module.JsonValue]:
        _ = archive_runner
        _ = cleanup_preview_runner
        emit(json.dumps({"status": "error"}, sort_keys=True))
        return {"status": "error"}

    monkeypatch.setattr(cli_module, "_run_visual_demo", run_demo)

    result = RUNNER.invoke(cli_module.app, ["demo"])

    assert result.exit_code == 1


@pytest.mark.unit()
def test_run_visual_demo_reports_bucket_story_and_cleanup_preview(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
    tmp_path: Path,
) -> None:
    settings = AppSettings.from_env(base_env)
    settings = AppSettings(
        source=settings.source,
        destination=settings.destination,
        path_filters=settings.path_filters,
        retention_days=60,
        cleanup_enabled=False,
        max_workers=settings.max_workers,
        run_timeout=settings.run_timeout,
        temp_dir=tmp_path / "runtime-temp",
        log_level=settings.log_level,
        log_dir=settings.log_dir,
    )
    state: dict[str, list[object]] = {
        settings.source.bucket: [
            _listed("demo/old.txt", 61),
            _listed("demo/recent.txt", 10),
        ],
        settings.destination.bucket: [],
    }

    class FakeReport:
        def as_dict(self) -> dict[str, demo_module.JsonValue]:
            return {"status": "ok", "checked_at": "2026-04-24T12:00:00+00:00"}

    class FakeArchiveBucket:
        bucket: str

        def __init__(self, _client: object, bucket: str, _temp_dir: Path) -> None:
            self.bucket = bucket

        def versioning_state(self) -> str:
            return "Enabled"

        def list_source_objects(self, _versioning_state: str) -> Iterable[object]:
            return tuple(state[self.bucket])

    def archive_runner(_settings: AppSettings, _log_file: Path) -> dict[str, demo_module.JsonValue]:
        state[settings.destination.bucket] = [_listed("demo/old.txt", 61)]
        return {
            "status": "ok",
            "phases": {
                "list": {"status": "ok", "failure_count": 0},
                "copy": {"status": "ok", "failure_count": 0},
                "verify": {"status": "ok", "failure_count": 0},
                "cleanup": {"status": "skipped", "failure_count": 0},
            },
        }

    def cleanup_runner(_settings: AppSettings, _log_file: Path) -> dict[str, demo_module.JsonValue]:
        return {
            "status": "ok",
            "cleanup_preview": {
                "cleanup_enabled_in_settings": False,
                "manifest_file": str(settings.temp_dir / "cleanup-preview-demo.json"),
                "object_count": 1,
                "entries": [
                    {
                        "key": "demo/old.txt",
                        "size": 10,
                        "last_modified_utc": "2024-02-19T00:00:00+00:00",
                        "version_id": "v1",
                    }
                ],
            },
        }

    def fake_health_check(_settings: AppSettings, _log_file: Path) -> FakeReport:
        return FakeReport()

    lines: list[str] = []
    monkeypatch.setattr(demo_module, "run_health_check", fake_health_check)
    monkeypatch.setattr(demo_module, "build_s3_client", _fake_build_client)
    monkeypatch.setattr(demo_module, "S3ArchiveBucket", FakeArchiveBucket)

    summary = demo_module.run_visual_demo(
        settings,
        settings.log_dir / "s3-archiver.log",
        archive_runner=archive_runner,
        cleanup_preview_runner=cleanup_runner,
        emit=lines.append,
        now=lambda: datetime(2024, 4, 20, tzinfo=UTC),
    )

    assert summary["status"] == "ok"
    manifest = cast(dict[str, object], summary["archive_manifest"])
    assert manifest["object_count"] == 1
    cleanup_preview = cast(dict[str, object], summary["cleanup_preview"])
    assert cleanup_preview["object_count"] == 1
    snapshots = cast(dict[str, object], summary["snapshots"])
    before_archive = cast(dict[str, object], snapshots["before_archive"])
    after_archive = cast(dict[str, object], snapshots["after_archive"])
    assert before_archive["source_object_count"] == 2
    assert after_archive["destination_object_count"] == 1
    assert summary["cleanup_preview_left_bucket_state_unchanged"] is True
    assert any("== Archive Candidates ==" in line for line in lines)
    assert any("DELETE key=demo/old.txt" in line for line in lines)
    assert json.loads(lines[-1])["status"] == "ok"


@pytest.mark.unit()
def test_run_visual_demo_uses_default_utc_clock(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
    tmp_path: Path,
) -> None:
    settings = AppSettings.from_env(base_env)
    settings = AppSettings(
        source=settings.source,
        destination=settings.destination,
        path_filters=settings.path_filters,
        retention_days=60,
        cleanup_enabled=False,
        max_workers=settings.max_workers,
        run_timeout=settings.run_timeout,
        temp_dir=tmp_path / "runtime-temp",
        log_level=settings.log_level,
        log_dir=settings.log_dir,
    )

    class FrozenDateTime:
        @staticmethod
        def now(*, tz: object) -> datetime:
            _ = tz
            return datetime(2024, 4, 20, tzinfo=UTC)

    class FakeReport:
        def as_dict(self) -> dict[str, demo_module.JsonValue]:
            return {"status": "ok", "checked_at": "2026-04-24T12:00:00+00:00"}

    class FakeArchiveBucket:
        bucket: str

        def __init__(self, _client: object, bucket: str, _temp_dir: Path) -> None:
            self.bucket = bucket

        def versioning_state(self) -> str:
            return "Enabled"

        def list_source_objects(self, _versioning_state: str) -> Iterable[object]:
            return (_listed("demo/old.txt", 61),)

    def archive_runner(_settings: AppSettings, _log_file: Path) -> dict[str, demo_module.JsonValue]:
        return {
            "status": "ok",
            "phases": {
                "list": {"status": "ok", "failure_count": 0},
                "copy": {"status": "ok", "failure_count": 0},
                "verify": {"status": "ok", "failure_count": 0},
                "cleanup": {"status": "skipped", "failure_count": 0},
            },
        }

    def cleanup_runner(_settings: AppSettings, _log_file: Path) -> dict[str, demo_module.JsonValue]:
        return {
            "status": "ok",
            "cleanup_preview": {
                "cleanup_enabled_in_settings": False,
                "manifest_file": str(settings.temp_dir / "cleanup-preview-demo.json"),
                "object_count": 1,
                "entries": [
                    {
                        "key": "demo/old.txt",
                        "size": 10,
                        "last_modified_utc": "2024-02-19T00:00:00+00:00",
                        "version_id": "v1",
                    }
                ],
            },
        }

    def fake_health_check(_settings: AppSettings, _log_file: Path) -> FakeReport:
        return FakeReport()

    monkeypatch.setattr(demo_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(demo_module, "run_health_check", fake_health_check)
    monkeypatch.setattr(demo_module, "build_s3_client", _fake_build_client)
    monkeypatch.setattr(demo_module, "S3ArchiveBucket", FakeArchiveBucket)

    summary = demo_module.run_visual_demo(
        settings,
        settings.log_dir / "s3-archiver.log",
        archive_runner=archive_runner,
        cleanup_preview_runner=cleanup_runner,
        emit=lambda _line: None,
    )

    assert summary["run_started_at_utc"] == "2024-04-20T00:00:00+00:00"
