"""Tests for settings loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.settings import AppSettings, S3Provider
from s3_archiver_core.temp_files import default_temp_dir

from tests.unit.settings_fakes import dual_env as _dual_env


@pytest.mark.parametrize(
    "key",
    [
        "ARCHIVER_RETENTION_DAYS",
        "ARCHIVER_ENABLE_CLEANUP",
        "ARCHIVER_MAX_WORKERS",
        "S3_SOURCE_PATH_WHITELIST_ENABLED",
        "S3_SOURCE_PATH_WHITELIST",
        "S3_SOURCE_PATH_BLACKLIST_ENABLED",
        "S3_SOURCE_PATH_BLACKLIST",
    ],
)
@pytest.mark.unit()
def test_from_env_rejects_removed_archive_env_vars(tmp_path: Path, key: str) -> None:
    env = _dual_env(tmp_path)
    env[key] = "1"

    with pytest.raises(ConfigError, match=key):
        _ = AppSettings.from_env(env)


@pytest.mark.parametrize(
    "key",
    [
        "S3_SOURCE_PATH_WHITELIST_ENABLED",
        "S3_SOURCE_PATH_WHITELIST",
        "S3_SOURCE_PATH_BLACKLIST_ENABLED",
        "S3_SOURCE_PATH_BLACKLIST",
    ],
)
@pytest.mark.unit()
def test_from_env_allows_blank_removed_source_filter_env_vars(tmp_path: Path, key: str) -> None:
    env = _dual_env(tmp_path)
    env[key] = " "

    settings = AppSettings.from_env(env)

    assert settings.routes


@pytest.mark.unit()
def test_from_env_requires_config_json(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    _ = env.pop("ARCHIVER_CONFIG_JSON")

    with pytest.raises(ConfigError, match="ARCHIVER_CONFIG_JSON is required"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_builds_route_settings(tmp_path: Path) -> None:
    settings = AppSettings.from_env(_dual_env(tmp_path))

    assert settings.source.provider is S3Provider.OCI
    assert settings.destination.provider is S3Provider.LOCALSTACK
    assert settings.source.access_key_id == "source-access"
    assert settings.destination.access_key_id == "destination-access"
    assert settings.source.resolved_endpoint_url() == (
        "https://tenant.compat.objectstorage.eu-frankfurt-1.oraclecloud.com"
    )
    assert settings.destination.resolved_endpoint_url() == "http://localstack:4566"
    assert settings.run_timeout.days == 7
    assert settings.temp_dir == default_temp_dir()
    assert settings.cleanup_enabled is False


@pytest.mark.unit()
def test_archive_options_disable_native_copy_for_mixed_endpoints(tmp_path: Path) -> None:
    settings = AppSettings.from_env(_dual_env(tmp_path))
    options = ArchiveOptions.from_settings(settings)

    assert options.transfer_capabilities.native_copy is False
    assert options.transfer_capabilities.multipart_copy is False
    assert options.transfer_capabilities.streaming_upload is True
    assert options.transfer_capabilities.temp_file_backed is True
    assert options.transfer_capabilities.streaming_limit_bytes > 1


@pytest.mark.unit()
def test_legacy_source_properties_proxy_source_location(tmp_path: Path) -> None:
    settings = AppSettings.from_env(_dual_env(tmp_path))

    assert settings.provider is settings.source.provider
    assert settings.access_key_id == settings.source.access_key_id
    assert settings.secret_access_key == settings.source.secret_access_key
    assert settings.region == settings.source.region
    assert settings.bucket == settings.source.bucket
    assert settings.addressing_style is settings.source.addressing_style
    assert settings.resolved_endpoint_url() == settings.source.resolved_endpoint_url()
