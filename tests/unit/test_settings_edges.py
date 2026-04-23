"""Edge-case tests for settings parsing."""

from __future__ import annotations

from pathlib import Path

import pytest
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.settings import AppSettings, S3AddressingStyle, S3LocationSettings, S3Provider

from tests.unit.settings_fakes import dual_env as _dual_env


@pytest.mark.unit()
def test_from_env_rejects_invalid_scalar_values(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["LOG_LEVEL"] = "trace"
    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        _ = AppSettings.from_env(env)

    env = _dual_env(tmp_path)
    env["ARCHIVER_ENABLE_CLEANUP"] = "maybe"
    with pytest.raises(ConfigError, match="ARCHIVER_ENABLE_CLEANUP"):
        _ = AppSettings.from_env(env)

    env = _dual_env(tmp_path)
    env["ARCHIVER_MAX_WORKERS"] = "many"
    with pytest.raises(ConfigError, match="ARCHIVER_MAX_WORKERS"):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
@pytest.mark.parametrize(
    ("raw_value", "message"),
    [
        ("[", "S3_SOURCE_PATH_WHITELIST"),
        ("{}", "S3_SOURCE_PATH_WHITELIST"),
    ],
)
def test_from_env_rejects_malformed_path_filter_json(
    tmp_path: Path,
    raw_value: str,
    message: str,
) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PATH_WHITELIST"] = raw_value

    with pytest.raises(ConfigError, match=message):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_treats_blank_path_filter_as_empty_array(tmp_path: Path) -> None:
    env = _dual_env(tmp_path)
    env["S3_SOURCE_PATH_WHITELIST"] = ""
    env["ARCHIVER_ENABLE_CLEANUP"] = "false"

    settings = AppSettings.from_env(env)

    assert settings.path_filters.whitelist == ()
    assert settings.path_filters.includes("anything") is True
    assert settings.cleanup_enabled is False


@pytest.mark.unit()
@pytest.mark.parametrize(
    "endpoint_url",
    [
        "http://localstack:4566?debug=true",
        "ftp://localstack",
        "http://localstack:bad",
    ],
)
def test_from_env_rejects_invalid_endpoint_url_shapes(
    tmp_path: Path,
    endpoint_url: str,
) -> None:
    env = _dual_env(tmp_path)
    env["S3_DESTINATION_ENDPOINT_URL"] = endpoint_url

    with pytest.raises(ConfigError, match="S3_DESTINATION_ENDPOINT_URL"):
        _ = AppSettings.from_env(env)


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


@pytest.mark.unit()
def test_oci_endpoint_resolution_requires_namespace() -> None:
    location = S3LocationSettings(
        provider=S3Provider.OCI,
        access_key_id="access",
        secret_access_key="secret",
        region="eu-frankfurt-1",
        bucket="bucket",
        namespace=None,
        iam_user_ocid="ocid1.user.oc1..example",
        endpoint_url=None,
        addressing_style=S3AddressingStyle.PATH,
    )

    with pytest.raises(ConfigError, match="S3_NAMESPACE"):
        _ = location.resolved_endpoint_url()
