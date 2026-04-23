"""CLI entrypoint."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import typer
from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult, run_archive
from s3_archiver_core.archive_lock import FileArchiveRunLock
from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.errors import (
    ArchiveRunError,
    ConfigError,
    HealthCheckError,
    LoggingError,
    S3ArchiverError,
)
from s3_archiver_core.health import run_health_check
from s3_archiver_core.logging_config import configure_logging
from s3_archiver_core.s3 import build_s3_client
from s3_archiver_core.settings import AppSettings
from s3_archiver_core.temp_files import prepare_runtime_temp_dir

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]


app: typer.Typer = typer.Typer(add_completion=False, invoke_without_command=True)
CONFIG_ERROR_EXIT_CODE = 2
LOGGING_ERROR_EXIT_CODE = 3
HEALTH_CHECK_ERROR_EXIT_CODE = 4
DEFAULT_ENV_FILE = ".env"


@app.callback()
def root(ctx: typer.Context) -> None:
    """Run s3-archiver commands."""

    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(code=0)


@app.command()
def check() -> None:
    """Validate configuration, logging, and bucket access."""

    settings: AppSettings | None = None
    try:
        settings = AppSettings.from_env(_load_runtime_env())
        prepare_runtime_temp_dir(settings.temp_dir)
        log_file = configure_logging(settings)
        report = run_health_check(settings, log_file)
    except S3ArchiverError as exc:
        typer.echo(json.dumps(_error_payload(exc, settings), sort_keys=True), err=True)
        raise typer.Exit(code=_exit_code_for_error(exc)) from exc

    payload = report.as_dict()
    typer.echo(json.dumps(payload, sort_keys=True))


@app.command()
def archive() -> None:
    """Run one archive workflow invocation."""

    settings: AppSettings | None = None
    try:
        settings = AppSettings.from_env(_load_runtime_env())
        prepare_runtime_temp_dir(settings.temp_dir)
        log_file = configure_logging(settings)
        _ = run_health_check(settings, log_file)
        payload = _run_archive(settings, log_file)
    except S3ArchiverError as exc:
        typer.echo(json.dumps(_error_payload(exc, settings), sort_keys=True), err=True)
        raise typer.Exit(code=_exit_code_for_error(exc)) from exc

    if payload.get("status") == "error":
        typer.echo(json.dumps(payload, sort_keys=True), err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(payload, sort_keys=True))


def main() -> None:
    """Run the CLI app."""

    app()


def _exit_code_for_error(error: S3ArchiverError) -> int:
    if isinstance(error, ConfigError):
        return CONFIG_ERROR_EXIT_CODE
    if isinstance(error, LoggingError):
        return LOGGING_ERROR_EXIT_CODE
    if isinstance(error, HealthCheckError):
        return HEALTH_CHECK_ERROR_EXIT_CODE
    return 1


def _run_archive(settings: AppSettings, log_file: Path) -> dict[str, JsonValue]:
    try:
        source = S3ArchiveBucket(
            build_s3_client(settings.source), settings.source.bucket, settings.temp_dir
        )
        destination = S3ArchiveBucket(
            build_s3_client(settings.destination),
            settings.destination.bucket,
            settings.temp_dir,
        )
        result = run_archive(
            source,
            destination,
            ArchiveOptions.from_settings(settings),
            run_lock=FileArchiveRunLock(_archive_lock_path(settings)),
            debug_logger=_log_transfer_decision if settings.log_level == "DEBUG" else None,
        )
    except Exception as exc:
        raise ArchiveRunError(str(exc)) from exc
    if result.ok:
        return _archive_result_payload("ok", result, settings, log_file)
    return _archive_failure_payload(result, settings, log_file)


def _archive_lock_path(settings: AppSettings) -> Path:
    return settings.log_dir / "archive.lock"


def _archive_result_payload(
    status: str,
    result: ArchiveRunResult,
    settings: AppSettings,
    log_file: Path,
) -> dict[str, JsonValue]:
    return {
        "status": status,
        "run_id": result.run_id,
        "source_bucket": settings.source.bucket,
        "destination_bucket": settings.destination.bucket,
        "log_file": str(log_file),
        "manifest": {
            "object_count": len(result.manifest.entries),
            "run_started_at_utc": result.manifest.run_started_at_utc.isoformat(),
            "retention_cutoff_utc": result.manifest.retention_cutoff_utc.isoformat(),
        },
        "phases": {
            "copy": _phase_payload(result.copy),
            "verify": _phase_payload(result.verify),
            "cleanup": _phase_payload(result.cleanup),
        },
    }


def _archive_failure_payload(
    result: ArchiveRunResult,
    settings: AppSettings,
    log_file: Path,
) -> dict[str, JsonValue]:
    phase, detail = _first_archive_failure(result)
    payload = _archive_result_payload("error", result, settings, log_file)
    payload.update(
        {
            "phase": f"archive.{phase}",
            "field": None,
            "message": "archive run failed",
            "details": detail,
            "key": _failure_key(detail),
            "mismatch": detail,
        }
    )
    return payload


def _phase_payload(result: ArchivePhaseResult) -> dict[str, JsonValue]:
    return {
        "status": "ok" if result.ok else "error",
        "failure_count": len(result.failures),
        "failures": list(result.failures),
    }


def _first_archive_failure(result: ArchiveRunResult) -> tuple[str, str]:
    for phase in (result.copy, result.verify, result.cleanup):
        if phase.failures:
            return phase.phase, phase.failures[0]
    return "unknown", "archive run failed"


def _failure_key(detail: str) -> str | None:
    key, separator, _remainder = detail.partition(":")
    if separator == "":
        return None
    return key


def _error_payload(
    error: S3ArchiverError, settings: AppSettings | None = None
) -> dict[str, JsonValue]:
    if isinstance(error, ConfigError):
        phase = "startup.env_validation"
    elif isinstance(error, ArchiveRunError):
        phase = "archive.run"
    else:
        phase = "startup.preflight"
    return {
        "status": "error",
        "phase": phase,
        "field": _field_from_error_message(str(error)) if isinstance(error, ConfigError) else None,
        "message": str(error),
        "details": str(error),
        "source_bucket": settings.source.bucket if settings is not None else None,
        "destination_bucket": settings.destination.bucket if settings is not None else None,
        "key": None,
        "mismatch": None,
    }


def _log_transfer_decision(entry: ManifestEntry, strategy: str) -> None:
    logging.getLogger("s3_archiver.archive").debug(
        "archive transfer strategy selected",
        extra={
            "event": "archive.transfer.strategy_selected",
            "key": entry.key,
            "source_bucket": entry.source_bucket,
            "strategy": strategy,
        },
    )


def _field_from_error_message(message: str) -> str | None:
    first_token = message.partition(" ")[0]
    if (
        first_token.isidentifier()
        or first_token.startswith("S3_")
        or first_token.startswith("ARCHIVER_")
    ):
        return first_token
    return None


def _load_runtime_env() -> dict[str, str]:
    env_file = _selected_env_file()
    file_env = _parse_env_file(env_file) if env_file.is_file() else {}
    runtime_env = dict(file_env)
    runtime_env.update(os.environ)
    return runtime_env


def _selected_env_file() -> Path:
    env_file = os.environ.get("APP_ENV_FILE") or os.environ.get("ENV_FILE") or DEFAULT_ENV_FILE
    return Path(env_file)


def _parse_env_file(path: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped.removeprefix("export ").strip()
        key, separator, raw_value = stripped.partition("=")
        if separator == "" or key.strip() == "":
            raise ConfigError(f"Invalid env assignment in {path}:{line_number}")
        loaded[key.strip()] = _strip_optional_quotes(raw_value.strip())
    return loaded


def _strip_optional_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
