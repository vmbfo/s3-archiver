"""CLI entrypoint."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from s3_archiver_core.archive import ArchiveRunResult, run_archive
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

from s3_archiver_cli.env import load_runtime_env as _load_runtime_env
from s3_archiver_cli.error_logging import (
    archive_failure_payload as _archive_failure_payload,
)
from s3_archiver_cli.error_logging import (
    archive_result_payload as _archive_result_payload,
)
from s3_archiver_cli.error_logging import (
    error_payload as _error_payload,
)
from s3_archiver_cli.error_logging import (
    log_error_payload as _log_error_payload,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]


app: typer.Typer = typer.Typer(add_completion=False, invoke_without_command=True)
CONFIG_ERROR_EXIT_CODE = 2
LOGGING_ERROR_EXIT_CODE = 3
HEALTH_CHECK_ERROR_EXIT_CODE = 4


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
        payload = _error_payload(exc, settings)
        _log_error_payload(payload, exc)
        typer.echo(json.dumps(payload, sort_keys=True), err=True)
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
        payload = _run_archive(settings, log_file)
    except S3ArchiverError as exc:
        payload = _error_payload(exc, settings)
        _log_error_payload(payload, exc)
        typer.echo(json.dumps(payload, sort_keys=True), err=True)
        raise typer.Exit(code=_exit_code_for_error(exc)) from exc
    if not _emit_archive_payload(payload):
        raise typer.Exit(code=1)


@app.command()
def schedule(
    daily_at_utc: Annotated[str, typer.Option(envvar="ARCHIVER_SCHEDULE_UTC")] = "02:00",
) -> None:
    """Run one archive invocation per UTC day without catch-up replay."""

    settings: AppSettings | None = None
    try:
        settings = AppSettings.from_env(_load_runtime_env())
        prepare_runtime_temp_dir(settings.temp_dir)
        log_file = configure_logging(settings)
    except S3ArchiverError as exc:
        payload = _error_payload(exc, settings)
        _log_error_payload(payload, exc)
        typer.echo(json.dumps(payload, sort_keys=True), err=True)
        raise typer.Exit(code=_exit_code_for_error(exc)) from exc
    hour, minute = _parse_daily_at_utc(daily_at_utc)
    while True:
        _sleep_until_next_daily_tick(hour, minute)
        try:
            _ = _emit_archive_payload(_run_archive(settings, log_file))
        except S3ArchiverError as exc:
            payload = _error_payload(exc, settings)
            _log_error_payload(payload, exc)
            typer.echo(json.dumps(payload, sort_keys=True), err=True)


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
    started = datetime.now(tz=UTC)
    locked_run_id = uuid4().hex
    run_lock = FileArchiveRunLock(_archive_lock_path(settings), recovery_logger=_log_lock_recovery)
    if not run_lock.acquire(
        run_id=locked_run_id,
        run_started_at_utc=started,
        timeout=settings.run_timeout,
    ):
        raise ArchiveRunError("archive run lock is already held")
    try:
        _ = run_health_check(settings, log_file)
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
            run_started_at_utc=started,
            debug_logger=_log_transfer_decision if settings.log_level == "DEBUG" else None,
        )
        if result.run_id != locked_run_id:
            result = ArchiveRunResult(
                locked_run_id,
                result.manifest,
                result.copy,
                result.verify,
                result.cleanup,
                result.list,
            )
    except S3ArchiverError:
        raise
    except Exception as exc:
        raise ArchiveRunError(str(exc)) from exc
    finally:
        run_lock.release(run_id=locked_run_id)
    if result.ok:
        return _archive_result_payload("ok", result, settings, log_file)
    return _archive_failure_payload(result, settings, log_file)


def _archive_lock_path(settings: AppSettings) -> Path:
    return settings.log_dir / "archive.lock"


def _emit_archive_payload(payload: Mapping[str, JsonValue]) -> bool:
    if payload.get("status") == "error":
        _log_error_payload(payload)
        typer.echo(json.dumps(payload, sort_keys=True), err=True)
        return False
    typer.echo(json.dumps(payload, sort_keys=True))
    return True


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


def _log_lock_recovery(reason: str, payload: Mapping[str, object]) -> None:
    logging.getLogger("s3_archiver.archive").warning(
        "archive stale run lock recovered",
        extra={
            "event": "archive.lock.recovered",
            "reason": reason,
            "stale_run_id": payload.get("run_id"),
            "stale_run_started_at_utc": payload.get("run_started_at_utc"),
            "stale_hostname": payload.get("hostname"),
            "stale_pid": payload.get("pid"),
        },
    )


def _parse_daily_at_utc(value: str) -> tuple[int, int]:
    hour_text, separator, minute_text = value.partition(":")
    if separator != ":" or not hour_text.isdigit() or not minute_text.isdigit():
        raise typer.BadParameter("ARCHIVER_SCHEDULE_UTC must look like HH:MM")
    hour = int(hour_text)
    minute = int(minute_text)
    if hour not in range(24) or minute not in range(60):
        raise typer.BadParameter("ARCHIVER_SCHEDULE_UTC must look like HH:MM")
    return hour, minute


def _sleep_until_next_daily_tick(hour: int, minute: int) -> None:
    now = datetime.now(tz=UTC)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    logging.getLogger("s3_archiver.archive").info(
        "archive schedule waiting for next tick",
        extra={
            "event": "archive.schedule.waiting",
            "scheduled_at_utc": target.isoformat(),
            "sleep_seconds": max(int((target - now).total_seconds()), 0),
        },
    )
    time.sleep(max((target - now).total_seconds(), 0.0))
