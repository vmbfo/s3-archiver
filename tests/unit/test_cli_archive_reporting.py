"""Tests for CLI archive failure reporting."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
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


def _private_attr(module: object, name: str) -> object:
    return cast(object, getattr(module, name))


class ArchivePayload(TypedDict):
    status: str
    phase: NotRequired[str]
    key: NotRequired[str | None]
    message: NotRequired[str]
    details: NotRequired[str]
    source_bucket: NotRequired[str]
    destination_bucket: NotRequired[str]
    phases: NotRequired[dict[str, object]]


@pytest.mark.unit()
def test_archive_command_reports_lock_refusal_payload(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    _stub_runtime(monkeypatch, base_env)

    def run_core_archive(
        source: object,
        destination: object,
        options: ArchiveOptions,
        *,
        run_lock: object | None = None,
        **_kwargs: object,
    ) -> ArchiveRunResult:
        _ = (source, destination, options, run_lock, _kwargs)
        raise RuntimeError("archive run lock is already held")

    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive"])

    assert result.exit_code == 1
    payload = _load_payload(result.stderr)
    assert payload["status"] == "error"
    assert payload.get("phase") == "archive.run"
    assert payload.get("message") == "archive run lock is already held"
    assert payload.get("source_bucket") == "archive-bucket"
    assert payload.get("destination_bucket") == "destination-bucket"
    assert payload.get("key") is None


@pytest.mark.unit()
def test_archive_command_reports_timeout_and_skipped_later_phases(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    _stub_runtime(monkeypatch, base_env)
    logged_error_payloads: list[Mapping[str, object]] = []

    def run_core_archive(
        source: object,
        destination: object,
        options: ArchiveOptions,
        *,
        run_lock: object | None = None,
        **_kwargs: object,
    ) -> ArchiveRunResult:
        _ = (source, destination, options, run_lock, _kwargs)
        return _archive_result(
            copy=ArchivePhaseResult("copy", ("archive run timed out",)),
            verify=ArchivePhaseResult("verify", skipped=True),
            cleanup=ArchivePhaseResult("cleanup", skipped=True),
        )

    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)
    monkeypatch.setattr(cli_module, "_log_error_payload", logged_error_payloads.append)

    result = RUNNER.invoke(cli_module.app, ["archive"])

    assert result.exit_code == 1
    payload = _load_payload(result.stderr)
    phases = _phase_payloads(payload)
    assert payload.get("phase") == "archive.copy"
    assert payload.get("field") == "ARCHIVER_RUN_TIMEOUT"
    assert payload.get("message") == "archive run timed out"
    assert payload.get("details") == "archive run timed out"
    assert payload.get("key") is None
    assert payload.get("reason") == "archive_run_timeout"
    assert payload.get("timed_out") is True
    assert phases["copy"]["status"] == "error"
    assert phases["verify"]["status"] == "skipped"
    assert phases["cleanup"]["status"] == "skipped"
    assert any(
        payload.get("phase") == "archive.copy" and payload.get("timed_out") is True
        for payload in logged_error_payloads
    )


@pytest.mark.unit()
def test_archive_command_reports_error_when_skipped_phase_has_failures(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    _stub_runtime(monkeypatch, base_env)

    def run_core_archive(
        source: object,
        destination: object,
        options: ArchiveOptions,
        *,
        run_lock: object | None = None,
        **_kwargs: object,
    ) -> ArchiveRunResult:
        _ = (source, destination, options, run_lock, _kwargs)
        return _archive_result(
            copy=ArchivePhaseResult("copy", ("old.txt: mismatch",), skipped=True)
        )

    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive"])

    assert result.exit_code == 1
    copy_phase = _phase_payloads(_load_payload(result.stderr))["copy"]
    assert copy_phase["status"] == "error"
    assert copy_phase["failure_count"] == 1
    assert copy_phase["failures"] == ["old.txt: mismatch"]


@pytest.mark.unit()
def test_archive_command_wires_lock_recovery_logger(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    _stub_runtime(monkeypatch, base_env)
    recovery_loggers: list[object] = []

    class RecordingLock:
        def __init__(self, _path: Path, **kwargs: object) -> None:
            recovery_loggers.append(kwargs.get("recovery_logger"))

        def acquire(self, *, run_id: str, run_started_at_utc: object, timeout: object) -> bool:
            _ = (run_id, run_started_at_utc, timeout)
            return True

        def release(self, *, run_id: str) -> None:
            _ = run_id

    def run_core_archive(
        source: object,
        destination: object,
        options: ArchiveOptions,
        *,
        run_lock: object | None = None,
        **_kwargs: object,
    ) -> ArchiveRunResult:
        _ = (source, destination, options, run_lock, _kwargs)
        return _archive_result()

    monkeypatch.setattr(cli_module, "FileArchiveRunLock", RecordingLock)
    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive"])

    assert result.exit_code == 0
    assert callable(recovery_loggers[0])


@pytest.mark.unit()
def test_run_archive_recovers_stale_prior_host_lock_before_archive_work(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    _stub_runtime(monkeypatch, base_env)
    settings = AppSettings.from_env(base_env)
    lock_path = Path(base_env["LOG_DIR"]) / "archive.lock"
    stale_payload = {
        "hostname": "prior-host",
        "pid": 4321,
        "run_id": "stale-run",
        "run_started_at_utc": datetime(2026, 4, 20, tzinfo=UTC).isoformat(),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _ = lock_path.write_text(json.dumps(stale_payload), encoding="utf-8")
    events: list[str] = []

    def log_recovery(reason: str, payload: Mapping[str, object]) -> None:
        events.append(f"recovery:{reason}")
        assert payload == stale_payload

    def run_health(_settings: AppSettings, _log_file: Path) -> object:
        events.append("health")
        return object()

    def build_client(location: S3LocationSettings) -> object:
        events.append(f"build:{location.bucket}")
        return object()

    def run_core_archive(
        source: object,
        destination: object,
        options: ArchiveOptions,
        *,
        run_lock: object | None = None,
        **_kwargs: object,
    ) -> ArchiveRunResult:
        _ = (source, destination, options, run_lock, _kwargs)
        events.append("run_archive")
        return _archive_result()

    monkeypatch.setattr(cli_module, "_log_lock_recovery", log_recovery)
    monkeypatch.setattr(cli_module, "run_health_check", run_health)
    monkeypatch.setattr(cli_module, "build_s3_client", build_client)
    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    run_archive = cast(
        Callable[[AppSettings, Path], dict[str, object]],
        _private_attr(cli_module, "_run_archive"),
    )
    payload = run_archive(settings, Path("/tmp/log"))

    assert payload["status"] == "ok"
    assert events == [
        "recovery:stale_lock_prior_host",
        "health",
        "build:archive-bucket",
        "build:destination-bucket",
        "run_archive",
    ]
    assert not lock_path.exists()


def _stub_runtime(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    monkeypatch.setattr(os, "environ", env)

    def configure(_settings: AppSettings) -> Path:
        return Path("/tmp/log")

    def run_health(_settings: AppSettings, _log_file: Path) -> object:
        return object()

    def build_client(_location: S3LocationSettings) -> object:
        return object()

    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "run_health_check", run_health)
    monkeypatch.setattr(cli_module, "build_s3_client", build_client)


def _load_payload(output: str) -> ArchivePayload:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = cast(object, json.loads(stripped))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return cast(ArchivePayload, cast(object, payload))
    raise AssertionError(f"expected JSON payload in output: {output!r}")


def _phase_payloads(payload: ArchivePayload) -> dict[str, dict[str, object]]:
    phases = payload.get("phases")
    assert isinstance(phases, dict)
    return {key: cast(dict[str, object], value) for key, value in phases.items()}


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
