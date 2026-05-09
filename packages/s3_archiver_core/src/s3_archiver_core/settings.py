from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from s3_archiver_core._settings_parse import LOCALSTACK_ENDPOINT_HOSTS, EnvDecoder
from s3_archiver_core._settings_parse import normalize_endpoint_url as _normalize_endpoint_url
from s3_archiver_core._settings_parse import normalize_endpoint_url_result as _endpoint_result
from s3_archiver_core._settings_parse import optional_env_result as _optional_result
from s3_archiver_core._settings_parse import parse_bool_result as _bool_result
from s3_archiver_core._settings_parse import parse_int_result as _int_result
from s3_archiver_core._settings_parse import parse_runtime_duration_result as _duration_result
from s3_archiver_core._settings_parse import parse_string_array_result as _string_array_result
from s3_archiver_core._settings_parse import require_env_result as _require_result
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
        if self.namespace is None:
            raise ConfigError("S3_NAMESPACE is required for OCI endpoint resolution")
        return f"https://{self.namespace}.compat.objectstorage.{self.region}.oraclecloud.com"

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

        decoder = EnvDecoder(env)
        log_level = env.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in _VALID_LOG_LEVELS:
            decoder.fail(
                "LOG_LEVEL", f"LOG_LEVEL must be one of {_VALID_LOG_LEVELS}, got {log_level!r}"
            )
        source = _load_s3_location(decoder, "SOURCE")
        destination = _load_s3_location(decoder, "DESTINATION")
        for field, location in (
            ("S3_SOURCE_ENDPOINT_URL", source),
            ("S3_DESTINATION_ENDPOINT_URL", destination),
        ):
            if location is None or location.provider is not S3Provider.LOCALSTACK:
                continue
            host = urlsplit(location.resolved_endpoint_url()).hostname
            if host not in LOCALSTACK_ENDPOINT_HOSTS:
                decoder.fail(
                    field, f"{field} host {host!r} is not allowed when provider=localstack"
                )
        path_filters = _load_path_filters(decoder)
        retention_days = decoder.consume(
            _int_result(env, "ARCHIVER_RETENTION_DAYS", default=60, minimum=1)
        )
        max_workers = decoder.consume(
            _int_result(env, "ARCHIVER_MAX_WORKERS", default=16, minimum=1)
        )
        cleanup_enabled = decoder.consume(
            _bool_result(env, "ARCHIVER_ENABLE_CLEANUP", default=False)
        )
        run_timeout = decoder.consume(
            _duration_result(env.get("ARCHIVER_RUN_TIMEOUT", "7d"), "ARCHIVER_RUN_TIMEOUT")
        )
        if (
            source is not None
            and destination is not None
            and source.storage_identity() == destination.storage_identity()
        ):
            decoder.fail(
                "ARCHIVER_STORAGE_LOCATION",
                "ARCHIVER_STORAGE_LOCATION must differ between source and destination",
            )
        decoder.finish()
        assert source is not None and destination is not None and path_filters is not None
        assert retention_days is not None and max_workers is not None
        assert cleanup_enabled is not None and run_timeout is not None
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


def _load_s3_location(decoder: EnvDecoder, side: str) -> S3LocationSettings | None:
    env = decoder.env
    prefix = f"S3_{side}_"
    provider_key = f"{prefix}PROVIDER"
    provider_value = decoder.consume(_require_result(env, provider_key))
    namespace = decoder.consume(_optional_result(env, f"{prefix}NAMESPACE"))
    iam_user_ocid = decoder.consume(_optional_result(env, f"{prefix}IAM_USER_OCID"))
    addressing_key = f"{prefix}ADDRESSING_STYLE"
    addressing_value = env.get(addressing_key, "path").strip().lower()
    if provider_value is None:
        return None
    provider_text = provider_value.lower()
    if provider_text not in _VALID_PROVIDERS:
        decoder.fail(
            provider_key,
            f"{provider_key} must be one of {_VALID_PROVIDERS}, got {provider_text!r}",
        )
        return None
    if addressing_value not in _VALID_ADDRESSING_STYLES:
        decoder.fail(
            addressing_key,
            f"{addressing_key} must be one of {_VALID_ADDRESSING_STYLES}, got {addressing_value!r}",
        )
        return None
    provider = S3Provider(provider_text)
    if provider is S3Provider.OCI and namespace is None:
        decoder.fail(f"{prefix}NAMESPACE", f"{prefix}NAMESPACE is required when {provider_key}=oci")
        return None
    if provider is S3Provider.OCI and iam_user_ocid is None:
        decoder.fail(
            f"{prefix}IAM_USER_OCID",
            f"{prefix}IAM_USER_OCID is required when {provider_key}=oci",
        )
        return None
    endpoint_url = decoder.consume(_optional_result(env, f"{prefix}ENDPOINT_URL"))
    if endpoint_url is not None:
        endpoint_url = decoder.consume(
            _endpoint_result(endpoint_url, field=f"{prefix}ENDPOINT_URL")
        )
        if endpoint_url is None:
            return None
    access_key_id = decoder.consume(_require_result(env, f"{prefix}ACCESS_KEY_ID"))
    secret_access_key = decoder.consume(_require_result(env, f"{prefix}SECRET_ACCESS_KEY"))
    region = decoder.consume(_require_result(env, f"{prefix}REGION"))
    bucket = decoder.consume(_require_result(env, f"{prefix}BUCKET"))
    if access_key_id is None or secret_access_key is None or region is None or bucket is None:
        return None
    return S3LocationSettings(
        provider=provider,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        region=region,
        bucket=bucket,
        namespace=namespace,
        iam_user_ocid=iam_user_ocid,
        endpoint_url=endpoint_url,
        addressing_style=S3AddressingStyle(addressing_value),
    )


def _load_path_filters(decoder: EnvDecoder) -> PathFilterSettings | None:
    env = decoder.env
    whitelist_enabled = decoder.consume(
        _bool_result(env, "S3_SOURCE_PATH_WHITELIST_ENABLED", default=False)
    )
    blacklist_enabled = decoder.consume(
        _bool_result(env, "S3_SOURCE_PATH_BLACKLIST_ENABLED", default=False)
    )
    whitelist = decoder.consume(_string_array_result(env, "S3_SOURCE_PATH_WHITELIST"))
    blacklist = decoder.consume(_string_array_result(env, "S3_SOURCE_PATH_BLACKLIST"))
    if whitelist_enabled and blacklist_enabled:
        decoder.fail(
            "S3_SOURCE_PATH_FILTER_MODE",
            "S3_SOURCE_PATH_FILTER_MODE allows only one enabled filter mode",
        )
        return None
    if (
        whitelist_enabled is None
        or blacklist_enabled is None
        or whitelist is None
        or blacklist is None
    ):
        return None
    return PathFilterSettings(whitelist_enabled, blacklist_enabled, whitelist, blacklist)
