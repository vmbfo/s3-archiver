"""CLI entrypoint."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Protocol

import typer
from s3_archiver_core.errors import (
    ConfigError,
    HealthCheckError,
    LoggingError,
    S3ArchiverError,
)
from s3_archiver_core.health import run_health_check
from s3_archiver_core.logging_config import configure_logging
from s3_archiver_core.settings import AppSettings

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]


class ArchiveRunner(Protocol):
    """Callable archive workflow hook, supplied by the archive implementation."""

    def __call__(self, settings: AppSettings) -> dict[str, JsonValue]:
        """Run one archive invocation."""
        ...


app: typer.Typer = typer.Typer(add_completion=False, invoke_without_command=True)
archive_runner: ArchiveRunner | None = None
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

    try:
        settings = AppSettings.from_env(_load_runtime_env())
        log_file = configure_logging(settings)
        report = run_health_check(settings, log_file)
    except S3ArchiverError as exc:
        typer.echo(json.dumps(_error_payload(exc), sort_keys=True), err=True)
        raise typer.Exit(code=_exit_code_for_error(exc)) from exc

    payload = report.as_dict()
    typer.echo(json.dumps(payload, sort_keys=True))


@app.command()
def archive() -> None:
    """Run one archive workflow invocation."""

    try:
        settings = AppSettings.from_env(_load_runtime_env())
        log_file = configure_logging(settings)
        payload = _run_archive(settings, log_file)
    except S3ArchiverError as exc:
        typer.echo(json.dumps(_error_payload(exc), sort_keys=True), err=True)
        raise typer.Exit(code=_exit_code_for_error(exc)) from exc

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
    _ = log_file
    if archive_runner is None:
        raise HealthCheckError("archive runner is not available")
    return archive_runner(settings)


def _error_payload(error: S3ArchiverError) -> dict[str, JsonValue]:
    phase = "startup.env_validation" if isinstance(error, ConfigError) else "startup.preflight"
    return {
        "status": "error",
        "phase": phase,
        "field": _field_from_error_message(str(error)) if isinstance(error, ConfigError) else None,
        "message": str(error),
        "details": str(error),
        "source_bucket": None,
        "destination_bucket": None,
        "key": None,
        "mismatch": None,
    }


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
