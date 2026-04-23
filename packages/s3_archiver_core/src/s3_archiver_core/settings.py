from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from s3_archiver_core._settings_parse import normalize_endpoint_url as _normalize_endpoint_url
from s3_archiver_core._settings_parse import optional_env as _optional
from s3_archiver_core._settings_parse import parse_bool as _parse_bool
from s3_archiver_core._settings_parse import parse_int as _parse_int
from s3_archiver_core._settings_parse import parse_runtime_duration as _parse_duration
from s3_archiver_core._settings_parse import parse_string_array as _parse_string_array
from s3_archiver_core._settings_parse import require_env as _require
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.temp_files import default_temp_dir


class S3Provider(StrEnum):
    """Supported S3-compatible storage providers."""

    OCI = "oci"
    LOCALSTACK = "localstack"


class S3AddressingStyle(StrEnum):
    """Supported S3 addressing styles."""

    PATH = "path"
    VIRTUAL = "virtual"


_VALID_PROVIDERS = frozenset({provider.value for provider in S3Provider})
_VALID_ADDRESSING_STYLES = frozenset({style.value for style in S3AddressingStyle})
_VALID_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})
_LOCALSTACK_ENDPOINT_HOSTS = frozenset(
    {"127.0.0.1", "localhost", "localstack", "localstack-alt", "localhost.localstack.cloud"}
)


@dataclass(frozen=True, slots=True)
class StorageLocationIdentity:
    """Normalized physical storage location for source/destination safety checks."""

    provider: S3Provider
    endpoint_url: str
    region: str | None
    namespace: str | None
    bucket: str


@dataclass(frozen=True, slots=True)
class S3LocationSettings:
    """Validated settings for one S3 source or destination location."""

    provider: S3Provider
    access_key_id: str
    secret_access_key: str
    region: str
    bucket: str
    namespace: str | None
    iam_user_ocid: str | None
    endpoint_url: str | None
    addressing_style: S3AddressingStyle

    def resolved_endpoint_url(self) -> str:
        """Return the explicit or provider-default endpoint URL."""

        if self.endpoint_url is not None:
            return self.endpoint_url
        if self.provider is S3Provider.LOCALSTACK:
            return "http://localstack:4566"
        namespace = self.namespace
        if namespace is None:
            raise ConfigError("S3_NAMESPACE is required for OCI endpoint resolution")
        return f"https://{namespace}.compat.objectstorage.{self.region}.oraclecloud.com"

    def storage_identity(self) -> StorageLocationIdentity:
        """Return the normalized physical bucket identity."""

        return StorageLocationIdentity(
            provider=self.provider,
            endpoint_url=_normalize_endpoint_url(self.resolved_endpoint_url()),
            region=self.region if self.provider is S3Provider.OCI else None,
            namespace=self.namespace if self.provider is S3Provider.OCI else None,
            bucket=self.bucket,
        )


@dataclass(frozen=True, slots=True)
class PathFilterSettings:
    """Validated source path filter configuration."""

    whitelist_enabled: bool
    blacklist_enabled: bool
    whitelist: tuple[str, ...]
    blacklist: tuple[str, ...]

    def includes(self, key: str) -> bool:
        """Return whether a source key is allowed by the configured filters."""

        if self.whitelist_enabled:
            return any(key.startswith(prefix) for prefix in self.whitelist)
        if self.blacklist_enabled:
            return not any(key.startswith(prefix) for prefix in self.blacklist)
        return True


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

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AppSettings:
        """Parse and validate application settings from environment values."""

        log_level = env.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _VALID_LOG_LEVELS:
            raise ConfigError(f"LOG_LEVEL must be one of {_VALID_LOG_LEVELS}, got {log_level!r}")

        source = _load_s3_location(env, "SOURCE")
        destination = _load_s3_location(env, "DESTINATION")
        _validate_localstack_endpoint(source, "S3_SOURCE_ENDPOINT_URL")
        _validate_localstack_endpoint(destination, "S3_DESTINATION_ENDPOINT_URL")
        path_filters = _load_path_filters(env)
        retention_days = _parse_int(env, "ARCHIVER_RETENTION_DAYS", default=60, minimum=1)
        max_workers = _parse_int(env, "ARCHIVER_MAX_WORKERS", default=16, minimum=1)
        cleanup_enabled = _parse_bool(env, "ARCHIVER_ENABLE_CLEANUP", default=False)
        run_timeout = _parse_duration(env.get("ARCHIVER_RUN_TIMEOUT", "7d"), "ARCHIVER_RUN_TIMEOUT")

        if source.storage_identity() == destination.storage_identity():
            raise ConfigError(
                "ARCHIVER_STORAGE_LOCATION must differ between source and destination"
            )

        return cls(
            source=source,
            destination=destination,
            path_filters=path_filters,
            retention_days=retention_days,
            cleanup_enabled=cleanup_enabled,
            max_workers=max_workers,
            run_timeout=run_timeout,
            temp_dir=Path(env.get("ARCHIVER_TEMP_DIR", str(default_temp_dir()))),
            log_level=log_level,
            log_dir=Path(env.get("LOG_DIR", "/var/log/s3-archiver")),
        )

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


def _load_s3_location(env: Mapping[str, str], side: str) -> S3LocationSettings:
    prefix = f"S3_{side}_"
    provider_key = f"{prefix}PROVIDER"
    provider_value = _require(env, provider_key).lower()
    addressing_key = f"{prefix}ADDRESSING_STYLE"
    addressing_value = env.get(addressing_key, "path").strip().lower()
    namespace = _optional(env, f"{prefix}NAMESPACE")
    iam_user_ocid = _optional(env, f"{prefix}IAM_USER_OCID")

    if provider_value not in _VALID_PROVIDERS:
        raise ConfigError(
            f"{provider_key} must be one of {_VALID_PROVIDERS}, got {provider_value!r}"
        )
    if addressing_value not in _VALID_ADDRESSING_STYLES:
        raise ConfigError(
            f"{addressing_key} must be one of {_VALID_ADDRESSING_STYLES}, got {addressing_value!r}"
        )
    provider = S3Provider(provider_value)
    if provider is S3Provider.OCI and namespace is None:
        namespace_key = f"{prefix}NAMESPACE"
        raise ConfigError(f"{namespace_key} is required when {provider_key}=oci")
    if provider is S3Provider.OCI and iam_user_ocid is None:
        raise ConfigError(f"{prefix}IAM_USER_OCID is required when {provider_key}=oci")

    endpoint_url = _optional(env, f"{prefix}ENDPOINT_URL")
    if endpoint_url is not None:
        _ = _normalize_endpoint_url(endpoint_url, field=f"{prefix}ENDPOINT_URL")

    return S3LocationSettings(
        provider=provider,
        access_key_id=_require(env, f"{prefix}ACCESS_KEY_ID"),
        secret_access_key=_require(env, f"{prefix}SECRET_ACCESS_KEY"),
        region=_require(env, f"{prefix}REGION"),
        bucket=_require(env, f"{prefix}BUCKET"),
        namespace=namespace,
        iam_user_ocid=iam_user_ocid,
        endpoint_url=endpoint_url,
        addressing_style=S3AddressingStyle(addressing_value),
    )


def _load_path_filters(env: Mapping[str, str]) -> PathFilterSettings:
    whitelist_enabled = _parse_bool(env, "S3_SOURCE_PATH_WHITELIST_ENABLED", default=False)
    blacklist_enabled = _parse_bool(env, "S3_SOURCE_PATH_BLACKLIST_ENABLED", default=False)
    if whitelist_enabled and blacklist_enabled:
        raise ConfigError("S3_SOURCE_PATH_FILTER_MODE allows only one enabled filter mode")
    return PathFilterSettings(
        whitelist_enabled=whitelist_enabled,
        blacklist_enabled=blacklist_enabled,
        whitelist=_parse_string_array(env, "S3_SOURCE_PATH_WHITELIST"),
        blacklist=_parse_string_array(env, "S3_SOURCE_PATH_BLACKLIST"),
    )


def _validate_localstack_endpoint(location: S3LocationSettings, field: str) -> None:
    if location.provider is not S3Provider.LOCALSTACK:
        return
    host = urlsplit(location.resolved_endpoint_url()).hostname
    if host not in _LOCALSTACK_ENDPOINT_HOSTS:
        raise ConfigError(f"{field} host {host!r} is not allowed when provider=localstack")
