"""Unit tests for archive option parsing."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.settings import AppSettings

from tests.unit.settings_fakes import dual_env


@pytest.mark.unit()
def test_options_env_defaults_to_route_runtime_baseline() -> None:
    assert ArchiveOptions.from_env({}).run_timeout == timedelta(days=7)
    options = ArchiveOptions.from_env({"ARCHIVER_RUN_TIMEOUT": "1h"})
    assert options.run_timeout == timedelta(hours=1)


@pytest.mark.unit()
@pytest.mark.parametrize(
    "key",
    ["ARCHIVER_RETENTION_DAYS", "ARCHIVER_ENABLE_CLEANUP", "ARCHIVER_MAX_WORKERS"],
)
def test_options_reject_removed_env_values(key: str) -> None:
    with pytest.raises(ConfigError, match=key):
        _ = ArchiveOptions.from_env({key: "1"})


@pytest.mark.unit()
def test_options_reject_invalid_env_values() -> None:
    with pytest.raises(ConfigError, match="ARCHIVER_RUN_TIMEOUT"):
        _ = ArchiveOptions.from_env({"ARCHIVER_RUN_TIMEOUT": "soon"})


@pytest.mark.unit()
def test_options_enable_native_copy_when_credentials_differ_on_same_endpoint(
    tmp_path: Path,
) -> None:
    env = dual_env(tmp_path)
    env["S3_SOURCE_PROVIDER"] = "localstack"
    env["S3_SOURCE_ENDPOINT_URL"] = "http://localhost:4566"
    env["S3_DESTINATION_ENDPOINT_URL"] = "http://localhost:4566"

    options = ArchiveOptions.from_settings(AppSettings.from_env(env))

    assert options.transfer_capabilities.native_copy is True
    assert options.transfer_capabilities.multipart_copy is True


@pytest.mark.unit()
def test_options_enable_native_copy_for_same_credentials_on_same_endpoint(tmp_path: Path) -> None:
    env = dual_env(tmp_path)
    env["S3_SOURCE_PROVIDER"] = "localstack"
    env["S3_SOURCE_ENDPOINT_URL"] = "http://localhost:4566"
    env["S3_DESTINATION_ENDPOINT_URL"] = "http://localhost:4566"
    env["S3_DESTINATION_ACCESS_KEY_ID"] = env["S3_SOURCE_ACCESS_KEY_ID"]
    env["S3_DESTINATION_SECRET_ACCESS_KEY"] = env["S3_SOURCE_SECRET_ACCESS_KEY"]

    options = ArchiveOptions.from_settings(AppSettings.from_env(env))

    assert options.transfer_capabilities.native_copy is True
    assert options.transfer_capabilities.multipart_copy is True


@pytest.mark.unit()
def test_options_disable_native_copy_for_cross_provider_pair(tmp_path: Path) -> None:
    options = ArchiveOptions.from_settings(AppSettings.from_env(dual_env(tmp_path)))

    assert options.transfer_capabilities.native_copy is False
    assert options.transfer_capabilities.multipart_copy is False
    assert options.transfer_capabilities.streaming_upload is True
    assert options.transfer_capabilities.temp_file_backed is True


@pytest.mark.unit()
def test_options_use_route_paths_from_settings(tmp_path: Path) -> None:
    options = ArchiveOptions.from_settings(AppSettings.from_env(dual_env(tmp_path)))

    assert options.routes[0].source_path == ""
    assert options.routes[0].destination_path == ""
