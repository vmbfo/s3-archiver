"""Tests for scheduler command wiring and subprocess execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import s3_archiver_cli.main as cli_module
import s3_archiver_cli.scheduled_archive as scheduled_archive_module
from s3_archiver_core.settings import AppSettings
from typer.testing import CliRunner

RUNNER = CliRunner()


@pytest.mark.unit()
def test_schedule_command_runs_scheduled_archive_after_first_tick(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    scheduled_runs: list[str] = []
    sleep_calls = 0

    def configure(_settings: AppSettings) -> Path:
        return Path("/tmp/log")

    def fake_sleep_until_tick(hour: int, minute: int) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise RuntimeError("stop scheduler test")
        assert (hour, minute) == (4, 5)

    def fake_run_scheduled_archive(
        settings: AppSettings, log_file: Path, **_kwargs: object
    ) -> None:
        _ = settings
        assert log_file == Path("/tmp/log")
        scheduled_runs.append("run")

    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "_sleep_until_next_daily_tick", fake_sleep_until_tick)
    monkeypatch.setattr(cli_module, "run_scheduled_archive", fake_run_scheduled_archive)

    result = RUNNER.invoke(cli_module.app, ["schedule", "--daily-at-utc", "04:05"])

    assert isinstance(result.exception, RuntimeError)
    assert "stop scheduler test" in str(result.exception)
    assert scheduled_runs == ["run"]


@pytest.mark.unit()
def test_schedule_command_continues_after_child_error_on_later_tick(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    events: list[str] = []
    sleep_calls = 0
    run_attempts = 0

    def configure(_settings: AppSettings) -> Path:
        return Path("/tmp/log")

    def fake_sleep_until_tick(hour: int, minute: int) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        events.append(f"sleep-{sleep_calls}")
        assert (hour, minute) == (4, 5)
        if sleep_calls == 3:
            raise RuntimeError("stop scheduler test")

    def fake_run_scheduled_archive(
        _settings: AppSettings, _log_file: Path, **_kwargs: object
    ) -> None:
        nonlocal run_attempts
        run_attempts += 1
        events.append(f"run-{run_attempts}")
        if run_attempts == 1:
            payload = {"message": "archive run lock is already held", "phase": "archive.run"}
            cli_module.typer.echo(json.dumps(payload, sort_keys=True), err=True)
            return
        cli_module.typer.echo(json.dumps({"status": "ok", "run_id": "scheduled-run"}))

    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "_sleep_until_next_daily_tick", fake_sleep_until_tick)
    monkeypatch.setattr(cli_module, "run_scheduled_archive", fake_run_scheduled_archive)

    result = RUNNER.invoke(cli_module.app, ["schedule", "--daily-at-utc", "04:05"])

    assert isinstance(result.exception, RuntimeError)
    assert "stop scheduler test" in str(result.exception)
    assert events == ["sleep-1", "run-1", "sleep-2", "run-2", "sleep-3"]
    assert _load_payload(result.stderr)["message"] == "archive run lock is already held"
    assert _load_payload(result.stdout)["status"] == "ok"


@pytest.mark.unit()
def test_scheduled_archive_command_targets_archive_cli() -> None:
    assert scheduled_archive_module.scheduled_archive_command() == [
        sys.executable,
        "-c",
        "from s3_archiver_cli.main import main; main()",
        "archive",
    ]


@pytest.mark.unit()
def test_run_scheduled_archive_relays_child_process_streams(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    settings = AppSettings.from_env(base_env)
    stdout_messages: list[str] = []
    stderr_messages: list[str] = []
    commands: list[list[str]] = []

    def fake_run_command(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout='{"status":"ok"}\n', stderr="warn\n")

    scheduled_archive_module.run_scheduled_archive(
        settings,
        Path("/tmp/log"),
        command=["archive"],
        run_command=fake_run_command,
        stdout_echo=stdout_messages.append,
        stderr_echo=stderr_messages.append,
    )

    assert commands == [["archive"]]
    assert stdout_messages == ['{"status":"ok"}\n']
    assert stderr_messages == ["warn\n"]


@pytest.mark.unit()
def test_run_scheduled_archive_times_out_and_recovers_stale_lock(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    settings = AppSettings.from_env(base_env)
    stderr_messages: list[str] = []
    logged_payloads: list[dict[str, object]] = []
    acquired: list[str] = []
    released: list[str] = []

    class RecordingLock:
        def __init__(self, path: Path, **_kwargs: object) -> None:
            assert path == Path(base_env["LOG_DIR"]) / "archive.lock"

        def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: object) -> bool:
            assert run_started_at_utc.tzinfo == UTC
            acquired.append(run_id)
            _ = timeout
            return True

        def release(self, *, run_id: str) -> None:
            released.append(run_id)

    def fake_run_command(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        error = subprocess.TimeoutExpired(command, timeout=1, output="", stderr="")
        raise error

    monkeypatch.setattr(scheduled_archive_module, "FileArchiveRunLock", RecordingLock)

    scheduled_archive_module.run_scheduled_archive(
        settings,
        Path("/tmp/log"),
        command=["archive"],
        run_command=fake_run_command,
        stderr_echo=stderr_messages.append,
        log_error=lambda payload: logged_payloads.append(cast(dict[str, object], dict(payload))),
    )

    assert len(acquired) == 1
    assert released == acquired
    payload = _load_payload(stderr_messages[-1])
    assert payload["field"] == "ARCHIVER_RUN_TIMEOUT"
    assert payload["message"] == "archive run timed out"
    assert payload["phase"] == "archive.run"
    assert payload["reason"] == "archive_run_timeout"
    assert payload["timed_out"] is True
    assert logged_payloads[-1]["phase"] == "archive.run"


def _load_payload(output: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads(output))
