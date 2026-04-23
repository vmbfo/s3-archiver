"""Tests for settings loading and validation."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.settings import AppSettings, S3Provider
from s3_archiver_core.temp_files import default_temp_dir


@pytest.mark.unit()
def test_from_env_builds_dual_s3_settings(tmp_path: Path) -> None:
    settings = AppSettings.from_env(_dual_env(tmp_path))

    assert settings.source.provider is S3Provider.OCI
    assert settings.destination.provider is S3Provider.LOCALSTACK
    assert settings.source.access_key_id == "source-access"
    assert settings.destination.access_key_id == "destination-access"
    assert settings.source.resolved_endpoint_url() == (
        "https://tenant.compat.objectstorage.eu-frankfurt-1.oraclecloud.com"
    )
    assert settings.destination.resolved_endpoint_url() == "http://localstack:4566"


@pytest.mark.unit()
def test_from_env_applies_archive_defaults(tmp_path: Path) -> None:
    settings = AppSettings.from_env(_dual_env(tmp_path))

    assert settings.retention_days == 60
    assert settings.cleanup_enabled is False
    assert settings.max_workers == 16
    assert settings.run_timeout == timedelta(days=7)
    assert settings.temp_dir == default_temp_dir()


@pytest.mark.unit()
def test_from_env_parses_archive_runtime_options(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["ARCHIVER_RETENTION_DAYS"] = "30"
    env["ARCHIVER_ENABLE_CLEANUP"] = "true"
    env["ARCHIVER_MAX_WORKERS"] = "4"
    env["ARCHIVER_RUN_TIMEOUT"] = "12h"
    env["ARCHIVER_TEMP_DIR"] = str(tmp_path / "archive-temp")

    settings = AppSettings.from_env(env)

    assert settings.retention_days == 30
    assert settings.cleanup_enabled is True
    assert settings.max_workers == 4
    assert settings.run_timeout == timedelta(hours=12)
    assert settings.temp_dir == tmp_path / "archive-temp"


@pytest.mark.unit()
def test_archive_options_disable_native_copy_for_mixed_endpoints(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    settings = AppSettings.from_env(env)

    options = ArchiveOptions.from_settings(settings)

    assert options.transfer_capabilities.native_copy is False
    assert options.transfer_capabilities.multipart_copy is False
    assert options.transfer_capabilities.streaming_upload is True


@pytest.mark.unit()
def test_from_env_rejects_invalid_runtime_values(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["ARCHIVER_MAX_WORKERS"] = "0"

    with pytest.raises(ConfigError, match="ARCHIVER_MAX_WORKERS"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_zero_retention_days(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["ARCHIVER_RETENTION_DAYS"] = "0"

    with pytest.raises(ConfigError, match="ARCHIVER_RETENTION_DAYS"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_invalid_run_timeout(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["ARCHIVER_RUN_TIMEOUT"] = "soon"

    with pytest.raises(ConfigError, match="ARCHIVER_RUN_TIMEOUT"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_bare_number_run_timeout(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["ARCHIVER_RUN_TIMEOUT"] = "30"

    with pytest.raises(ConfigError, match="ARCHIVER_RUN_TIMEOUT"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_invalid_provider(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PROVIDER"] = "broken"

    with pytest.raises(ConfigError, match="S3_SOURCE_PROVIDER"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_legacy_single_bucket_env(tmp_path: Path) -> None:
    env = {
        "S3_PROVIDER": "oci",
        "S3_ACCESS_KEY_ID": "access-key",
        "S3_SECRET_ACCESS_KEY": "secret-key",
        "S3_REGION": "eu-frankfurt-1",
        "S3_NAMESPACE": "tenant",
        "S3_BUCKET": "archive-bucket",
        "OCI_IAM_USER_OCID": "ocid1.user.oc1..example",
        "LOG_DIR": str(tmp_path / "logs"),
    }

    with pytest.raises(ConfigError, match="S3_SOURCE_PROVIDER"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_invalid_addressing_style(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_DESTINATION_ADDRESSING_STYLE"] = "broken"

    with pytest.raises(ConfigError, match="S3_DESTINATION_ADDRESSING_STYLE"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_invalid_endpoint_url(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_DESTINATION_ENDPOINT_URL"] = "localstack:4566"

    with pytest.raises(ConfigError, match="S3_DESTINATION_ENDPOINT_URL"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_requires_oci_namespace_and_user(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    _ = env.pop("S3_SOURCE_NAMESPACE")

    with pytest.raises(ConfigError, match="S3_SOURCE_NAMESPACE"):
        _ = AppSettings.from_env(env)

    env = _dual_env(tmp_path)
    _ = env.pop("S3_SOURCE_IAM_USER_OCID")
    with pytest.raises(ConfigError, match="S3_SOURCE_IAM_USER_OCID"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_same_normalized_storage_location_with_different_credentials(
    tmp_path: Path,
) -> None:
    env = _dual_env(tmp_path)
    env["S3_DESTINATION_PROVIDER"] = "oci"
    env["S3_DESTINATION_REGION"] = env["S3_SOURCE_REGION"]
    env["S3_DESTINATION_BUCKET"] = env["S3_SOURCE_BUCKET"]
    env["S3_DESTINATION_NAMESPACE"] = env["S3_SOURCE_NAMESPACE"]
    env["S3_DESTINATION_IAM_USER_OCID"] = "ocid1.user.oc1..other"

    with pytest.raises(ConfigError, match="ARCHIVER_STORAGE_LOCATION"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_allows_same_bucket_name_at_different_storage_locations(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_DESTINATION_BUCKET"] = env["S3_SOURCE_BUCKET"]

    settings = AppSettings.from_env(env)

    assert settings.source.bucket == settings.destination.bucket
    assert settings.source.storage_identity() != settings.destination.storage_identity()


@pytest.mark.unit()
def test_from_env_rejects_same_localstack_bucket_with_different_regions(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PROVIDER"] = "localstack"
    env["S3_SOURCE_REGION"] = "us-west-2"
    env["S3_SOURCE_BUCKET"] = "shared-bucket"
    env["S3_SOURCE_ENDPOINT_URL"] = "http://localhost:4566/"
    env["S3_DESTINATION_BUCKET"] = "shared-bucket"
    env["S3_DESTINATION_ENDPOINT_URL"] = "http://localhost:4566"
    _ = env.pop("S3_SOURCE_NAMESPACE")
    _ = env.pop("S3_SOURCE_IAM_USER_OCID")

    with pytest.raises(ConfigError, match="ARCHIVER_STORAGE_LOCATION"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_same_storage_location_with_equivalent_default_ports(
    tmp_path: Path,
) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PROVIDER"] = "localstack"
    env["S3_SOURCE_REGION"] = "us-east-1"
    env["S3_SOURCE_BUCKET"] = "shared-bucket"
    env["S3_SOURCE_ENDPOINT_URL"] = "http://localhost:80"
    env["S3_DESTINATION_BUCKET"] = "shared-bucket"
    env["S3_DESTINATION_ENDPOINT_URL"] = "http://localhost"
    _ = env.pop("S3_SOURCE_NAMESPACE")
    _ = env.pop("S3_SOURCE_IAM_USER_OCID")

    with pytest.raises(ConfigError, match="ARCHIVER_STORAGE_LOCATION"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_allows_same_bucket_when_non_default_ports_differ(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PROVIDER"] = "localstack"
    env["S3_SOURCE_REGION"] = "us-east-1"
    env["S3_SOURCE_BUCKET"] = "shared-bucket"
    env["S3_SOURCE_ENDPOINT_URL"] = "http://localhost:443"
    env["S3_DESTINATION_BUCKET"] = "shared-bucket"
    env["S3_DESTINATION_ENDPOINT_URL"] = "http://localhost"
    _ = env.pop("S3_SOURCE_NAMESPACE")
    _ = env.pop("S3_SOURCE_IAM_USER_OCID")

    settings = AppSettings.from_env(env)

    assert settings.source.storage_identity() != settings.destination.storage_identity()


@pytest.mark.unit()
def test_from_env_parses_source_path_whitelist(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PATH_WHITELIST_ENABLED"] = "true"
    env["S3_SOURCE_PATH_WHITELIST"] = '["daily/", "manual/"]'

    settings = AppSettings.from_env(env)

    assert settings.path_filters.includes("daily/report.json") is True
    assert settings.path_filters.includes("other/report.json") is False


@pytest.mark.unit()
def test_from_env_parses_source_path_blacklist(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PATH_BLACKLIST_ENABLED"] = "true"
    env["S3_SOURCE_PATH_BLACKLIST"] = '["tmp/"]'

    settings = AppSettings.from_env(env)

    assert settings.path_filters.includes("tmp/report.json") is False
    assert settings.path_filters.includes("daily/report.json") is True


@pytest.mark.unit()
def test_from_env_rejects_invalid_path_filter_array(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PATH_WHITELIST"] = '["ok", 3]'

    with pytest.raises(ConfigError, match="S3_SOURCE_PATH_WHITELIST"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_two_enabled_path_filter_modes(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PATH_WHITELIST_ENABLED"] = "true"
    env["S3_SOURCE_PATH_BLACKLIST_ENABLED"] = "true"

    with pytest.raises(ConfigError, match="S3_SOURCE_PATH_FILTER_MODE"):
        _ = AppSettings.from_env(env)


def _dual_env(tmp_path: Path) -> dict[str, str]:
    return {
        "S3_SOURCE_PROVIDER": "oci",
        "S3_SOURCE_ACCESS_KEY_ID": "source-access",
        "S3_SOURCE_SECRET_ACCESS_KEY": "source-secret",
        "S3_SOURCE_REGION": "eu-frankfurt-1",
        "S3_SOURCE_NAMESPACE": "tenant",
        "S3_SOURCE_BUCKET": "source-bucket",
        "S3_SOURCE_IAM_USER_OCID": "ocid1.user.oc1..source",
        "S3_SOURCE_ADDRESSING_STYLE": "path",
        "S3_DESTINATION_PROVIDER": "localstack",
        "S3_DESTINATION_ACCESS_KEY_ID": "destination-access",
        "S3_DESTINATION_SECRET_ACCESS_KEY": "destination-secret",
        "S3_DESTINATION_REGION": "us-east-1",
        "S3_DESTINATION_BUCKET": "destination-bucket",
        "S3_DESTINATION_ADDRESSING_STYLE": "path",
        "LOG_LEVEL": "INFO",
        "LOG_DIR": str(tmp_path / "logs"),
    }
