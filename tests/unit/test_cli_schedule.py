"""Tests for scheduler command wiring and subprocess execution."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import s3_archiver_cli.main as cli_module
import s3_archiver_cli.scheduled_archive as scheduled_archive_module
import typer
from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult
from s3_archiver_core.archive_manifest import ArchiveManifest
from s3_archiver_core.errors import ArchiveRunError, ConfigError, HealthCheckError
from s3_archiver_core.settings import AppSettings
from typer.testing import CliRunner

RUNNER = CliRunner()


def _private_attr(module: object, name: str) -> object:
    return cast(object, getattr(module, name))


_parse_daily_at_utc = cast(
    Callable[[str], tuple[int, int]],
    _private_attr(cli_module, "_parse_daily_at_utc"),
)
_run_archive = cast(
    Callable[[AppSettings, Path], dict[str, object]],
    _private_attr(cli_module, "_run_archive"),
)
_sleep_until_next_daily_tick = cast(
    Callable[[int, int], None],
    _private_attr(cli_module, "_sleep_until_next_daily_tick"),
)
_as_text = cast(
    Callable[[str | bytes | None], str],
    _private_attr(scheduled_archive_module, "_as_text"),
)
_stdout_echo = cast(Callable[[str], None], _private_attr(scheduled_archive_module, "_stdout_echo"))
_stderr_echo = cast(Callable[[str], None], _private_attr(scheduled_archive_module, "_stderr_echo"))


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
            typer.echo(json.dumps(payload, sort_keys=True), err=True)
            return
        typer.echo(json.dumps({"status": "ok", "run_id": "scheduled-run"}))

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
def test_schedule_command_returns_json_for_startup_config_errors() -> None:
    monkeypatch = pytest.MonkeyPatch()

    def raise_config_error(_: dict[str, str]) -> AppSettings:
        raise ConfigError("bad env")

    monkeypatch.setattr(AppSettings, "from_env", raise_config_error)
    try:
        result = RUNNER.invoke(cli_module.app, ["schedule"])
    finally:
        monkeypatch.undo()

    assert result.exit_code == cli_module.CONFIG_ERROR_EXIT_CODE
    assert _load_payload(result.stderr)["message"] == "bad env"


@pytest.mark.unit()
def test_schedule_command_logs_child_domain_errors_and_keeps_looping(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    sleep_calls = 0

    def configure(_settings: AppSettings) -> Path:
        return Path("/tmp/log")

    def fake_sleep_until_tick(hour: int, minute: int) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        assert (hour, minute) == (4, 5)
        if sleep_calls == 2:
            raise RuntimeError("stop scheduler test")

    def raise_child_error(_settings: AppSettings, _log_file: Path, **_kwargs: object) -> None:
        raise HealthCheckError("auth failed: denied")

    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "_sleep_until_next_daily_tick", fake_sleep_until_tick)
    monkeypatch.setattr(cli_module, "run_scheduled_archive", raise_child_error)

    result = RUNNER.invoke(cli_module.app, ["schedule", "--daily-at-utc", "04:05"])

    assert isinstance(result.exception, RuntimeError)
    assert "stop scheduler test" in str(result.exception)
    payload = _load_payload(result.stderr)
    assert payload["message"] == "auth failed: denied"
    assert payload["phase"] == "startup.preflight"


@pytest.mark.unit()
def test_parse_daily_at_utc_rejects_invalid_shapes() -> None:
    with pytest.raises(typer.BadParameter, match="HH:MM"):
        _ = _parse_daily_at_utc("04-05")

    with pytest.raises(typer.BadParameter, match="HH:MM"):
        _ = _parse_daily_at_utc("24:00")


@pytest.mark.unit()
def test_sleep_until_next_daily_tick_waits_until_future_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 4, 23, 4, 6, tzinfo=UTC)
    sleep_calls: list[float] = []
    contexts: list[dict[str, object]] = []
    logger = logging.getLogger("s3_archiver.archive")

    class FrozenDateTime:
        @staticmethod
        def now(*, tz: object) -> datetime:
            _ = tz
            return now

    def record_info(_message: str, *, extra: dict[str, object]) -> None:
        contexts.append(extra)

    monkeypatch.setattr(cli_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(time, "sleep", sleep_calls.append)
    monkeypatch.setattr(logger, "info", record_info)

    _sleep_until_next_daily_tick(4, 5)

    assert sleep_calls == [86340.0]
    assert contexts == [
        {
            "event": "archive.schedule.waiting",
            "scheduled_at_utc": "2026-04-24T04:05:00+00:00",
            "sleep_seconds": 86340,
        }
    ]


@pytest.mark.unit()
def test_sleep_until_next_daily_tick_waits_until_later_same_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 4, 23, 4, 4, 30, tzinfo=UTC)
    sleep_calls: list[float] = []
    logger = logging.getLogger("s3_archiver.archive")

    class FrozenDateTime:
        @staticmethod
        def now(*, tz: object) -> datetime:
            _ = tz
            return now

    monkeypatch.setattr(cli_module, "datetime", FrozenDateTime)
    def noop_info(_message: str, *, extra: dict[str, object]) -> None:
        _ = extra

    monkeypatch.setattr(time, "sleep", sleep_calls.append)
    monkeypatch.setattr(logger, "info", noop_info)

    _sleep_until_next_daily_tick(4, 5)

    assert sleep_calls == [30.0]


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


@pytest.mark.unit()
def test_run_scheduled_archive_timeout_skips_release_when_recovery_lock_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    settings = AppSettings.from_env(base_env)
    released: list[str] = []

    class RefusingLock:
        def __init__(self, path: Path, **_kwargs: object) -> None:
            assert path == Path(base_env["LOG_DIR"]) / "archive.lock"

        def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: object) -> bool:
            _ = (run_id, run_started_at_utc, timeout)
            return False

        def release(self, *, run_id: str) -> None:
            released.append(run_id)

    def fake_run_command(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, timeout=1, output=None, stderr=None)

    monkeypatch.setattr(scheduled_archive_module, "FileArchiveRunLock", RefusingLock)

    scheduled_archive_module.run_scheduled_archive(
        settings,
        Path("/tmp/log"),
        command=["archive"],
        run_command=fake_run_command,
    )

    assert released == []


@pytest.mark.unit()
def test_scheduled_archive_text_and_echo_helpers() -> None:
    echoed: list[tuple[str, bool, bool]] = []
    monkeypatch = pytest.MonkeyPatch()

    def record_echo(message: str, *, err: bool = False, nl: bool = True) -> None:
        echoed.append((message, err, nl))

    monkeypatch.setattr(typer, "echo", record_echo)
    try:
        assert _as_text(None) == ""
        assert _as_text(b"warn\n") == "warn\n"
        assert _as_text("ok\n") == "ok\n"
        _stdout_echo("ok\n")
        _stderr_echo("warn\n")
    finally:
        monkeypatch.undo()

    assert echoed == [("ok\n", False, False), ("warn\n", True, False)]


@pytest.mark.unit()
def test_run_archive_keeps_matching_run_id_and_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    settings = AppSettings.from_env(base_env)
    released: list[str] = []

    class FixedUuid:
        hex: str = "locked-run"

    class RecordingLock:
        def __init__(self, _path: Path, **_kwargs: object) -> None:
            return

        def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: object) -> bool:
            _ = (run_started_at_utc, timeout)
            return run_id == "locked-run"

        def release(self, *, run_id: str) -> None:
            released.append(run_id)

    def run_health(_settings: AppSettings, _log_file: Path) -> object:
        return object()

    def build_client(_location: object) -> object:
        return object()

    def run_core_archive(
        source: object,
        destination: object,
        options: object,
        *,
        run_started_at_utc: datetime,
        debug_logger: object | None = None,
    ) -> ArchiveRunResult:
        _ = (source, destination, options, run_started_at_utc, debug_logger)
        return _archive_result(run_id="locked-run")

    monkeypatch.setattr(cli_module, "uuid4", lambda: FixedUuid())
    monkeypatch.setattr(cli_module, "FileArchiveRunLock", RecordingLock)
    monkeypatch.setattr(cli_module, "run_health_check", run_health)
    monkeypatch.setattr(cli_module, "build_s3_client", build_client)
    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    payload = _run_archive(settings, Path("/tmp/log"))

    assert payload["status"] == "ok"
    assert released == ["locked-run"]


@pytest.mark.unit()
def test_run_archive_raises_when_lock_is_already_held(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    settings = AppSettings.from_env(base_env)

    class RefusingLock:
        def __init__(self, _path: Path, **_kwargs: object) -> None:
            return

        def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: object) -> bool:
            _ = (run_id, run_started_at_utc, timeout)
            return False

        def release(self, *, run_id: str) -> None:
            raise AssertionError(f"unexpected release for {run_id}")

    monkeypatch.setattr(cli_module, "FileArchiveRunLock", RefusingLock)

    with pytest.raises(ArchiveRunError, match="already held"):
        _ = _run_archive(settings, Path("/tmp/log"))


@pytest.mark.unit()
def test_run_archive_reraises_domain_errors_and_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    settings = AppSettings.from_env(base_env)
    released: list[str] = []

    class RecordingLock:
        def __init__(self, _path: Path, **_kwargs: object) -> None:
            return

        def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: object) -> bool:
            _ = (run_id, run_started_at_utc, timeout)
            return True

        def release(self, *, run_id: str) -> None:
            released.append(run_id)

    def raise_health_error(_settings: AppSettings, _log_file: Path) -> object:
        raise HealthCheckError("auth failed: denied")

    monkeypatch.setattr(cli_module, "FileArchiveRunLock", RecordingLock)
    monkeypatch.setattr(cli_module, "run_health_check", raise_health_error)

    with pytest.raises(HealthCheckError, match="auth failed: denied"):
        _ = _run_archive(settings, Path("/tmp/log"))

    assert len(released) == 1


def _archive_result(*, run_id: str = "run-id") -> ArchiveRunResult:
    return ArchiveRunResult(
        run_id=run_id,
        manifest=ArchiveManifest(
            run_started_at_utc=datetime.fromisoformat("2026-04-09T17:00:43+00:00"),
            retention_cutoff_utc=datetime.fromisoformat("2026-02-08T17:00:43+00:00"),
            entries=(),
        ),
        copy=ArchivePhaseResult("copy"),
        verify=ArchivePhaseResult("verify"),
        cleanup=ArchivePhaseResult("cleanup"),
    )


def _load_payload(output: str) -> dict[str, object]:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = cast(object, json.loads(stripped))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return cast(dict[str, object], cast(object, payload))
    raise AssertionError(f"expected JSON payload in output: {output!r}")
