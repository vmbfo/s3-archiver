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
    assert ArchiveOptions.from_env({}).run_timeout == timedelta(days=7)


@pytest.mark.unit()
def test_options_reject_invalid_env_values() -> None:
    with pytest.raises(ConfigError, match="ARCHIVER_ENABLE_CLEANUP"):
        _ = cleanup_enabled_from_env({"ARCHIVER_ENABLE_CLEANUP": "yes"})

    with pytest.raises(ConfigError, match="ARCHIVER_MAX_WORKERS"):
        _ = ArchiveOptions.from_env({"ARCHIVER_MAX_WORKERS": "0"})

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
