"""Tests for CLI archive wiring."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest
import s3_archiver_cli.main as cli_module
from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult
from s3_archiver_core.archive_manifest import ArchiveManifest
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.settings import AppSettings, S3LocationSettings
from typer.testing import CliRunner

RUNNER = CliRunner()


class ArchivePayload(TypedDict):
    """Typed CLI archive payload."""

    status: str
    phase: NotRequired[str]
    key: NotRequired[str | None]
    message: NotRequired[str]
    details: NotRequired[str]
    source_bucket: NotRequired[str]
    destination_bucket: NotRequired[str]
    phases: NotRequired[dict[str, object]]


@pytest.mark.unit()
def test_archive_command_runs_core_workflow_with_lock(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    built_locations: list[str] = []
    call_order: list[str] = []
    lock_paths: list[Path] = []
    option_retention_days: list[int] = []
    health_checked: list[Path] = []

    def configure(_: AppSettings) -> Path:
        return Path("/tmp/s3-archiver.log")

    def build_client(location: S3LocationSettings) -> object:
        built_locations.append(location.bucket)
        return object()

    def run_health(_settings: AppSettings, log_file: Path) -> object:
        call_order.append("health")
        health_checked.append(log_file)
        return object()

    class RecordingLock:
        def __init__(self, path: Path, **_kwargs: object) -> None:
            lock_paths.append(path)

        def acquire(self, *, run_id: str, run_started_at_utc: object, timeout: object) -> bool:
            _ = (run_id, run_started_at_utc, timeout)
            call_order.append("lock.acquire")
            return True

        def release(self, *, run_id: str) -> None:
            _ = run_id
            call_order.append("lock.release")

    def run_core_archive(
        source: object,
        destination: object,
        options: ArchiveOptions,
        *,
        run_lock: object | None = None,
        **_kwargs: object,
    ) -> ArchiveRunResult:
        _unused = (source, destination, run_lock, _kwargs)
        call_order.append("run_archive")
        option_retention_days.append(options.retention_days)
        return _archive_result()

    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "build_s3_client", build_client)
    monkeypatch.setattr(cli_module, "run_health_check", run_health)
    monkeypatch.setattr(cli_module, "FileArchiveRunLock", RecordingLock)
    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive"])

    assert result.exit_code == 0
    payload = _load_payload(result.stdout)
    assert payload["status"] == "ok"
    assert payload.get("source_bucket") == "archive-bucket"
    assert payload.get("destination_bucket") == "destination-bucket"
    assert built_locations == ["archive-bucket", "destination-bucket"]
    assert health_checked == [Path("/tmp/s3-archiver.log")]
    assert lock_paths == [Path(base_env["LOG_DIR"]) / "archive.lock"]
    assert option_retention_days == [60]
    assert call_order == ["lock.acquire", "health", "run_archive", "lock.release"]


@pytest.mark.unit()
def test_archive_command_reports_phase_failure_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)

    def configure(_: AppSettings) -> Path:
        return Path("/tmp/s3-archiver.log")

    def run_core_archive(
        source: object,
        destination: object,
        options: ArchiveOptions,
        *,
        run_lock: object | None = None,
        **_kwargs: object,
    ) -> ArchiveRunResult:
        _unused = (source, destination, options, run_lock, _kwargs)
        return _archive_result(copy=ArchivePhaseResult("copy", ("old.txt: denied",)))

    monkeypatch.setattr(cli_module, "configure_logging", configure)

    def run_health(_settings: AppSettings, _log_file: Path) -> object:
        return object()

    monkeypatch.setattr(cli_module, "run_health_check", run_health)

    def build_client(_location: S3LocationSettings) -> object:
        return object()

    monkeypatch.setattr(cli_module, "build_s3_client", build_client)
    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive"])

    assert result.exit_code == 1
    assert result.stdout == ""
    payload = _load_payload(result.stderr)
    assert payload["status"] == "error"
    assert payload.get("phase") == "archive.copy"
    assert payload.get("key") == "old.txt"


@pytest.mark.unit()
def test_archive_command_wires_debug_transfer_logger(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    base_env["LOG_LEVEL"] = "DEBUG"
    monkeypatch.setattr(os, "environ", base_env)
    debug_loggers: list[object] = []

    def configure(_settings: AppSettings) -> Path:
        return Path("/tmp/log")

    def run_health(_settings: AppSettings, _log_file: Path) -> object:
        return object()

    def build_client(_location: S3LocationSettings) -> object:
        return object()

    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "run_health_check", run_health)
    monkeypatch.setattr(cli_module, "build_s3_client", build_client)

    def run_core_archive(
        source: object,
        destination: object,
        options: ArchiveOptions,
        *,
        run_lock: object | None = None,
        debug_logger: object | None = None,
        **_kwargs: object,
    ) -> ArchiveRunResult:
        _unused = (source, destination, options, run_lock, _kwargs)
        debug_loggers.append(debug_logger)
        return _archive_result()

    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive"])

    assert result.exit_code == 0
    assert callable(debug_loggers[0])


@pytest.mark.unit()
def test_schedule_command_runs_archive_after_first_tick(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    scheduled_runs: list[dict[str, str]] = []
    sleep_calls = 0

    def configure(_settings: AppSettings) -> Path:
        return Path("/tmp/log")

    def fake_sleep_until_tick(hour: int, minute: int) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise RuntimeError("stop scheduler test")
        assert (hour, minute) == (4, 5)

    def fake_run_archive(_settings: AppSettings, _log_file: Path) -> dict[str, str]:
        payload = {"status": "ok", "run_id": "scheduled-run"}
        scheduled_runs.append(payload)
        return payload

    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "_sleep_until_next_daily_tick", fake_sleep_until_tick)
    monkeypatch.setattr(cli_module, "_run_archive", fake_run_archive)

    result = RUNNER.invoke(cli_module.app, ["schedule", "--daily-at-utc", "04:05"])

    assert isinstance(result.exception, RuntimeError)
    assert "stop scheduler test" in str(result.exception)
    assert scheduled_runs == [{"status": "ok", "run_id": "scheduled-run"}]


def _load_payload(output: str) -> ArchivePayload:
    return cast(ArchivePayload, json.loads(output))


def _archive_result(
    *,
    copy: ArchivePhaseResult | None = None,
    verify: ArchivePhaseResult | None = None,
    cleanup: ArchivePhaseResult | None = None,
) -> ArchiveRunResult:
    return ArchiveRunResult(
        run_id="run-id",
        manifest=ArchiveManifest(
            run_started_at_utc=datetime.fromisoformat("2026-04-09T17:00:43+00:00"),
            retention_cutoff_utc=datetime.fromisoformat("2026-02-08T17:00:43+00:00"),
            entries=(),
        ),
        copy=copy or ArchivePhaseResult("copy"),
        verify=verify or ArchivePhaseResult("verify"),
        cleanup=cleanup or ArchivePhaseResult("cleanup"),
    )
