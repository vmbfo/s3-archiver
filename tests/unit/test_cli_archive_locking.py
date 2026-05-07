"""Tests for CLI archive lock wiring."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
import s3_archiver_cli.main as cli_module
from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult
from s3_archiver_core.archive_manifest import ArchiveManifest
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.settings import AppSettings, S3LocationSettings
from typer.testing import CliRunner

RUNNER = CliRunner()


@pytest.mark.unit()
def test_archive_command_wires_lock_recovery_logger(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    recovery_loggers: list[object] = []

    def configure(_settings: AppSettings) -> Path:
        return Path("/tmp/log")

    def run_health(_settings: AppSettings, _log_file: Path) -> object:
        return object()

    def build_client(_location: S3LocationSettings) -> object:
        return object()

    class RecordingLock:
        def __init__(self, _path: Path, **kwargs: object) -> None:
            recovery_loggers.append(kwargs.get("recovery_logger"))

        def acquire(self, *, run_id: str, run_started_at_utc: object, timeout: object) -> bool:
            _ = (run_id, run_started_at_utc, timeout)
            return True

        def release(self, *, run_id: str) -> None:
            _ = run_id

    def run_core_archive(
        routes: tuple[object, ...],
        options: ArchiveOptions,
        *,
        run_started_at_utc: object | None = None,
        **_kwargs: object,
    ) -> ArchiveRunResult:
        _ = (routes, options, run_started_at_utc, _kwargs)
        return _archive_result()

    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "run_health_check", run_health)
    monkeypatch.setattr(cli_module, "build_s3_client", build_client)
    monkeypatch.setattr(cli_module, "FileArchiveRunLock", RecordingLock)
    monkeypatch.setattr(cli_module, "run_archive_routes", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive-once"])

    assert result.exit_code == 0
    assert callable(recovery_loggers[0])


def _archive_result() -> ArchiveRunResult:
    return ArchiveRunResult(
        run_id="run-id",
        manifest=ArchiveManifest(
            run_started_at_utc=datetime.fromisoformat("2026-04-09T17:00:43+00:00"),
            entries=(),
        ),
        copy=ArchivePhaseResult("copy"),
        verify=ArchivePhaseResult("verify"),
    )
