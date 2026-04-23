"""Unit tests for archive option parsing."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from s3_archiver_core.archive_options import ArchiveOptions, cleanup_enabled_from_env
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.settings import AppSettings

from tests.unit.settings_fakes import dual_env


@pytest.mark.unit()
def test_options_cleanup_defaults() -> None:
    assert cleanup_enabled_from_env({}) is False
    assert cleanup_enabled_from_env({"ARCHIVER_ENABLE_CLEANUP": "true"}) is True
    assert cleanup_enabled_from_env({"ARCHIVER_ENABLE_CLEANUP": " false "}) is False
    assert ArchiveOptions.from_env({}).run_timeout == timedelta(days=7)
    options = ArchiveOptions.from_env(
        {
            "ARCHIVER_RETENTION_DAYS": "30",
            "ARCHIVER_MAX_WORKERS": "2",
            "ARCHIVER_RUN_TIMEOUT": "1h",
        }
    )
    assert options.retention_days == 30
    assert options.max_workers == 2
    assert options.run_timeout == timedelta(hours=1)


@pytest.mark.unit()
def test_options_reject_invalid_env_values() -> None:
    with pytest.raises(ConfigError, match="ARCHIVER_ENABLE_CLEANUP"):
        _ = cleanup_enabled_from_env({"ARCHIVER_ENABLE_CLEANUP": "yes"})

    with pytest.raises(ConfigError, match="ARCHIVER_MAX_WORKERS"):
        _ = ArchiveOptions.from_env({"ARCHIVER_MAX_WORKERS": "0"})

    with pytest.raises(ConfigError, match="ARCHIVER_MAX_WORKERS"):
        _ = ArchiveOptions.from_env({"ARCHIVER_MAX_WORKERS": "many"})

    with pytest.raises(ConfigError, match="ARCHIVER_RUN_TIMEOUT"):
        _ = ArchiveOptions.from_env({"ARCHIVER_RUN_TIMEOUT": "soon"})


@pytest.mark.unit()
def test_options_disable_native_copy_when_credentials_differ(tmp_path: Path) -> None:
    env = dual_env(tmp_path)
    env["S3_SOURCE_PROVIDER"] = "localstack"
    env["S3_SOURCE_ENDPOINT_URL"] = "http://localhost:4566"
    env["S3_DESTINATION_ENDPOINT_URL"] = "http://localhost:4566"
    _ = env.pop("S3_SOURCE_NAMESPACE")
    _ = env.pop("S3_SOURCE_IAM_USER_OCID")

    options = ArchiveOptions.from_settings(AppSettings.from_env(env))

    assert options.transfer_capabilities.native_copy is False
    assert options.transfer_capabilities.multipart_copy is False


@pytest.mark.unit()
def test_options_enable_native_copy_for_same_credentials_on_same_endpoint(tmp_path: Path) -> None:
    env = dual_env(tmp_path)
    env["S3_SOURCE_PROVIDER"] = "localstack"
    env["S3_SOURCE_ENDPOINT_URL"] = "http://localhost:4566"
    env["S3_DESTINATION_ENDPOINT_URL"] = "http://localhost:4566"
    env["S3_DESTINATION_ACCESS_KEY_ID"] = env["S3_SOURCE_ACCESS_KEY_ID"]
    env["S3_DESTINATION_SECRET_ACCESS_KEY"] = env["S3_SOURCE_SECRET_ACCESS_KEY"]
    _ = env.pop("S3_SOURCE_NAMESPACE")
    _ = env.pop("S3_SOURCE_IAM_USER_OCID")

    options = ArchiveOptions.from_settings(AppSettings.from_env(env))

    assert options.transfer_capabilities.native_copy is True
    assert options.transfer_capabilities.multipart_copy is True


@pytest.mark.unit()
def test_options_preserve_path_filter_mode_from_settings(tmp_path: Path) -> None:
    whitelist_env = dual_env(tmp_path)
    whitelist_env["S3_SOURCE_PATH_WHITELIST_ENABLED"] = "true"
    whitelist_env["S3_SOURCE_PATH_WHITELIST"] = '["daily/"]'

    whitelist_options = ArchiveOptions.from_settings(AppSettings.from_env(whitelist_env))

    assert whitelist_options.source_filter.includes("daily/report.json") is True
    assert whitelist_options.source_filter.includes("tmp/report.json") is False

    blacklist_env = dual_env(tmp_path)
    blacklist_env["S3_SOURCE_PATH_BLACKLIST_ENABLED"] = "true"
    blacklist_env["S3_SOURCE_PATH_BLACKLIST"] = '["tmp/"]'

    blacklist_options = ArchiveOptions.from_settings(AppSettings.from_env(blacklist_env))

    assert blacklist_options.source_filter.includes("tmp/report.json") is False
    assert blacklist_options.source_filter.includes("daily/report.json") is True
