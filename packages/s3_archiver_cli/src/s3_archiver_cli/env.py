"""Runtime environment loading for the CLI."""

from __future__ import annotations

import os
from pathlib import Path

from s3_archiver_core.errors import ConfigError

DEFAULT_ENV_FILE = ".env"


def load_runtime_env() -> dict[str, str]:
    """Load the selected env file and overlay process environment variables."""

    env_file = selected_env_file()
    file_env = parse_env_file(env_file) if env_file.is_file() else {}
    runtime_env = dict(file_env)
    runtime_env.update(os.environ)
    return runtime_env


def selected_env_file() -> Path:
    """Return the env file selected by environment, or the default."""

    env_file = os.environ.get("APP_ENV_FILE") or os.environ.get("ENV_FILE") or DEFAULT_ENV_FILE
    return Path(env_file)


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE env files."""

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
        loaded[key.strip()] = strip_optional_quotes(raw_value.strip())
    return loaded


def strip_optional_quotes(value: str) -> str:
    """Remove matching single or double quotes around one env value."""

    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
