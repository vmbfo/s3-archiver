"""Legacy environment parsing for single-route compatibility."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from s3_archiver_core._settings_factory import AppSettingsFactory
from s3_archiver_core._settings_models import (
    CopyMode,
    PathFilterSettings,
    RouteSettings,
    S3AddressingStyle,
    S3LocationSettings,
    S3Provider,
)
from s3_archiver_core._settings_parse import LOCALSTACK_ENDPOINT_HOSTS, EnvDecoder
from s3_archiver_core._settings_parse import normalize_endpoint_url_result as _endpoint_result
from s3_archiver_core._settings_parse import optional_env_result as _optional_result
from s3_archiver_core._settings_parse import parse_bool_result as _bool_result
from s3_archiver_core._settings_parse import parse_int_result as _int_result
from s3_archiver_core._settings_parse import parse_runtime_duration_result as _duration_result
from s3_archiver_core._settings_parse import parse_string_array_result as _string_array_result
from s3_archiver_core._settings_parse import require_env_result as _require_result
from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.temp_files import default_temp_dir


def load_legacy_app_settings[T](
    settings_type: AppSettingsFactory[T],
    decoder: EnvDecoder,
    env: Mapping[str, str],
    log_level: str,
) -> T:
    """Build app settings from the legacy env shape."""

    source = load_s3_location(decoder, "SOURCE")
    destination = load_s3_location(decoder, "DESTINATION")
    for field, location in (
        ("S3_SOURCE_ENDPOINT_URL", source),
        ("S3_DESTINATION_ENDPOINT_URL", destination),
    ):
        if location is not None:
            _validate_localstack_endpoint_host(decoder, field, location)
    path_filters = _load_path_filters(decoder)
    retention_days = decoder.consume(
        _int_result(env, "ARCHIVER_RETENTION_DAYS", default=60, minimum=1)
    )
    max_workers = decoder.consume(_int_result(env, "ARCHIVER_MAX_WORKERS", default=16, minimum=1))
    cleanup_enabled = decoder.consume(_bool_result(env, "ARCHIVER_ENABLE_CLEANUP", default=False))
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
    return settings_type(
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
        routes=(
            RouteSettings(
                "default", ParserKind.FILENAME_TIMESTAMP, CopyMode.DAILY_TAR_GZ, source, destination
            ),
        ),
    )


def load_s3_location(decoder: EnvDecoder, side: str) -> S3LocationSettings | None:
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
    if provider_text not in {provider.value for provider in S3Provider}:
        decoder.fail(
            provider_key, f"{provider_key} must be one of {set(S3Provider)}, got {provider_text!r}"
        )
        return None
    if addressing_value not in {style.value for style in S3AddressingStyle}:
        decoder.fail(
            addressing_key,
            f"{addressing_key} must be one of {set(S3AddressingStyle)}, got {addressing_value!r}",
        )
        return None
    provider = S3Provider(provider_text)
    if provider is S3Provider.OCI and namespace is None:
        decoder.fail(f"{prefix}NAMESPACE", f"{prefix}NAMESPACE is required when {provider_key}=oci")
        return None
    if provider is S3Provider.OCI and iam_user_ocid is None:
        decoder.fail(
            f"{prefix}IAM_USER_OCID", f"{prefix}IAM_USER_OCID is required when {provider_key}=oci"
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
        provider,
        access_key_id,
        secret_access_key,
        region,
        bucket,
        namespace,
        iam_user_ocid,
        endpoint_url,
        S3AddressingStyle(addressing_value),
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


def _validate_localstack_endpoint_host(
    decoder: EnvDecoder, field: str, location: S3LocationSettings
) -> None:
    if location.provider is not S3Provider.LOCALSTACK:
        return
    host = urlsplit(location.resolved_endpoint_url()).hostname
    if host not in LOCALSTACK_ENDPOINT_HOSTS:
        decoder.fail(field, f"{field} host {host!r} is not allowed when provider=localstack")
