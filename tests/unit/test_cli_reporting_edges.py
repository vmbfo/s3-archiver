"""Edge-case tests for CLI reporting."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, override

import pytest
import s3_archiver_cli.main as cli_module
from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult
from s3_archiver_core.archive_manifest import ArchiveManifest, ManifestEntry
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.errors import ConfigError, HealthCheckError
from s3_archiver_core.s3 import S3ListedObject
from s3_archiver_core.settings import AppSettings, S3LocationSettings
from typer.testing import CliRunner

RUNNER = CliRunner()


class FailedWithoutFailures(ArchivePhaseResult):
    @property
    @override
    def ok(self) -> bool:
        return False


@pytest.mark.unit()
def test_archive_failure_payload_falls_back_when_no_phase_has_failures(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    stub_runtime(monkeypatch, base_env)

    def run_core_archive(
        routes: tuple[object, ...],
        options: ArchiveOptions,
        *,
        run_started_at_utc: object | None = None,
        **kwargs: object,
    ) -> ArchiveRunResult:
        _ = (routes, options, run_started_at_utc, kwargs)
        return archive_result(copy=FailedWithoutFailures("copy"))

    monkeypatch.setattr(cli_module, "run_archive_routes", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive-once"])

    assert result.exit_code == 1
    payload = load_payload(result.stderr)
    assert payload["phase"] == "archive.unknown"
    assert payload["details"] == "archive run failed"
    assert payload["key"] is None


@pytest.mark.unit()
def test_config_error_payload_ignores_messages_without_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_config_error(env: dict[str, str]) -> AppSettings:
        _ = env
        raise ConfigError("3 invalid runtime")

    monkeypatch.setattr(AppSettings, "from_env", raise_config_error)

    result = RUNNER.invoke(cli_module.app, ["check"])

    assert result.exit_code == cli_module.CONFIG_ERROR_EXIT_CODE
    assert load_payload(result.stderr)["field"] is None


@pytest.mark.unit()
@pytest.mark.parametrize(
    ("message", "field"),
    [
        ("source bucket versioning is Suspended", "source_bucket_versioning"),
        ("failed to access source bucket", "source_bucket_access"),
    ],
)
def test_check_command_maps_source_preflight_fields(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
    message: str,
    field: str,
) -> None:
    stub_runtime(monkeypatch, base_env)

    def raise_health_error(_settings: AppSettings, _log_file: Path) -> object:
        raise HealthCheckError(message)

    monkeypatch.setattr(cli_module, "run_health_check", raise_health_error)

    result = RUNNER.invoke(cli_module.app, ["check"])

    assert result.exit_code == cli_module.HEALTH_CHECK_ERROR_EXIT_CODE
    assert load_payload(result.stderr)["field"] == field


@pytest.mark.unit()
def test_debug_archive_run_logs_transfer_decision(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    base_env["LOG_LEVEL"] = "DEBUG"
    stub_runtime(monkeypatch, base_env)
    contexts = capture_logger_context(monkeypatch)

    def run_core_archive(
        routes: tuple[object, ...],
        options: ArchiveOptions,
        *,
        run_started_at_utc: object | None = None,
        debug_logger: Callable[[ManifestEntry, str], None] | None = None,
        **kwargs: object,
    ) -> ArchiveRunResult:
        _ = (routes, options, run_started_at_utc, kwargs)
        assert debug_logger is not None
        debug_logger(manifest_entry(), "streaming_upload")
        return archive_result()

    monkeypatch.setattr(cli_module, "run_archive_routes", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive-once"])

    assert result.exit_code == 0
    context = contexts[-1]
    assert context["event"] == "archive.transfer.strategy_selected"
    assert context["key"] == "old.txt"
    assert context["source_bucket"] == "source-bucket"
    assert context["strategy"] == "streaming_upload"


@pytest.mark.unit()
def test_archive_lock_recovery_logger_adds_structured_context(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    stub_runtime(monkeypatch, base_env)
    contexts = capture_logger_context(monkeypatch)

    class RecoveringLock:
        def __init__(self, _path: Path, **kwargs: object) -> None:
            recovery_logger = cast(
                Callable[[str, Mapping[str, object]], None],
                kwargs["recovery_logger"],
            )
            recovery_logger(
                "stale_lock_timed_out",
                {
                    "run_id": "run-1",
                    "run_started_at_utc": "2026-01-01T00:00:00+00:00",
                    "hostname": "host",
                    "pid": 123,
                },
            )

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
        **kwargs: object,
    ) -> ArchiveRunResult:
        _ = (routes, options, run_started_at_utc, kwargs)
        return archive_result()

    monkeypatch.setattr(cli_module, "FileArchiveRunLock", RecoveringLock)
    monkeypatch.setattr(cli_module, "run_archive_routes", run_core_archive)

    result = RUNNER.invoke(cli_module.app, ["archive-once"])

    assert result.exit_code == 0
    recovery_context = next(
        context for context in contexts if context.get("event") == "archive.lock.recovered"
    )
    assert recovery_context["reason"] == "stale_lock_timed_out"
    assert recovery_context["stale_run_id"] == "run-1"
    assert recovery_context["stale_hostname"] == "host"
    assert recovery_context["stale_pid"] == 123
    recovered_payload = load_payload(result.stderr)
    assert recovered_payload["reason"] == "archive_run_timeout"
    assert recovered_payload["recovered"] is True
    failure_context = next(
        context for context in contexts if context.get("event") == "s3_archiver.error"
    )
    assert failure_context["error_phase"] == "archive.run"
    assert failure_context["error_reason"] == "archive_run_timeout"
    assert failure_context["error_run_id"] == "run-1"
    assert failure_context["error_lock_recovery_reason"] == "stale_lock_timed_out"
    assert failure_context["error_recovered"] is True


def stub_runtime(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
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


def load_payload(output: str) -> dict[str, object]:
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


def capture_logger_context(monkeypatch: pytest.MonkeyPatch) -> list[Mapping[str, object]]:
    contexts: list[Mapping[str, object]] = []

    class RecordingHandler(logging.Handler):
        @override
        def emit(self, record: logging.LogRecord) -> None:
            contexts.append(cast(Mapping[str, object], record.__dict__))

    root_logger = logging.getLogger("s3_archiver")
    logger = logging.getLogger("s3_archiver.archive")
    handler = RecordingHandler()
    monkeypatch.setattr(root_logger, "handlers", [logging.NullHandler()])
    monkeypatch.setattr(logger, "handlers", [*logger.handlers, handler])
    monkeypatch.setattr(logger, "level", logging.DEBUG)
    return contexts


def manifest_entry() -> ManifestEntry:
    return ManifestEntry(
        source_bucket="source-bucket",
        key="old.txt",
        size=1,
        last_modified=datetime(2026, 1, 1, tzinfo=UTC),
        etag=None,
        version_id=None,
        object=cast(S3ListedObject, object()),
    )


def archive_result(copy: ArchivePhaseResult | None = None) -> ArchiveRunResult:
    return ArchiveRunResult(
        run_id="run-id",
        manifest=ArchiveManifest(
            run_started_at_utc=datetime.fromisoformat("2026-04-09T17:00:43+00:00"),
            retention_cutoff_utc=datetime.fromisoformat("2026-02-08T17:00:43+00:00"),
            entries=(),
        ),
        copy=copy or ArchivePhaseResult("copy"),
        verify=ArchivePhaseResult("verify"),
    )
