"""Validation for removed archive environment variables."""

from __future__ import annotations

from collections.abc import Mapping

from s3_archiver_core.errors import ConfigError

REMOVED_ARCHIVER_ENV = (
    "ARCHIVER_RETENTION_DAYS",
    "ARCHIVER_ENABLE_CLEANUP",
    "ARCHIVER_MAX_WORKERS",
)


def reject_removed_archiver_env(env: Mapping[str, str]) -> None:
    """Reject obsolete environment knobs now represented by route JSON."""

    for key in REMOVED_ARCHIVER_ENV:
        if env.get(key, "").strip() != "":
            raise ConfigError(f"{key} has been removed; configure ARCHIVER_CONFIG_JSON routes")
