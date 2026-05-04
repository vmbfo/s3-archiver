from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from s3_archiver_core._settings_models import (
    CopyMode,
    PathFilterSettings,
    RouteSettings,
    S3AddressingStyle,
    S3LocationSettings,
    S3Provider,
    StorageLocationIdentity,
)
from s3_archiver_core._settings_parse import EnvDecoder
from s3_archiver_core.errors import ConfigError

_VALID_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})

__all__ = (
    "AppSettings",
    "CopyMode",
    "PathFilterSettings",
    "RouteSettings",
    "S3AddressingStyle",
    "S3LocationSettings",
    "S3Provider",
    "StorageLocationIdentity",
)


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Validated runtime settings for the CLI and archive workflow."""

    source: S3LocationSettings
    destination: S3LocationSettings
    path_filters: PathFilterSettings
    retention_days: int
    cleanup_enabled: bool
    max_workers: int
    run_timeout: timedelta
    temp_dir: Path
    log_level: str
    log_dir: Path
    routes: tuple[RouteSettings, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AppSettings:
        """Parse and validate application settings from environment values."""

        decoder = EnvDecoder(env)
        log_level = env.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _VALID_LOG_LEVELS:
            decoder.fail(
                "LOG_LEVEL", f"LOG_LEVEL must be one of {_VALID_LOG_LEVELS}, got {log_level!r}"
            )
        config_json = env.get("ARCHIVER_CONFIG_JSON")
        if config_json is not None and config_json.strip() != "":
            from s3_archiver_core._route_config import load_app_settings_from_config_json

            return load_app_settings_from_config_json(cls, decoder, config_json, log_level)
        raise ConfigError("ARCHIVER_CONFIG_JSON is required")

    @property
    def provider(self) -> S3Provider:
        return self.source.provider

    @property
    def access_key_id(self) -> str:
        return self.source.access_key_id

    @property
    def secret_access_key(self) -> str:
        return self.source.secret_access_key

    @property
    def region(self) -> str:
        return self.source.region

    @property
    def bucket(self) -> str:
        return self.source.bucket

    @property
    def addressing_style(self) -> S3AddressingStyle:
        return self.source.addressing_style

    def resolved_endpoint_url(self) -> str:
        """Return the source endpoint URL for legacy callers."""

        return self.source.resolved_endpoint_url()
