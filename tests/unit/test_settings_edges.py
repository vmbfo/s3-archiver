"""Edge-case tests for settings parsing helpers."""

from __future__ import annotations

from datetime import timedelta

import pytest
from s3_archiver_core._settings_parse import (
    EnvDecoder,
    optional_env,
    parse_bool,
    parse_bool_result,
    parse_int,
    parse_runtime_duration,
    parse_string_array,
    require_env,
)
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.settings import S3AddressingStyle, S3LocationSettings, S3Provider


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


@pytest.mark.unit()
def test_parse_result_boundary_captures_issue_without_raising() -> None:
    result = parse_bool_result(
        {"ARCHIVER_ENABLE_CLEANUP": "maybe"}, "ARCHIVER_ENABLE_CLEANUP", default=False
    )

    assert result.ok is False
    assert result.value is None
    assert result.issue is not None
    assert result.issue.field == "ARCHIVER_ENABLE_CLEANUP"


@pytest.mark.unit()
def test_parse_wrappers_cover_successful_result_boundary() -> None:
    env = {
        "BOOL": "true",
        "COUNT": "2",
        "ARRAY": '["prefix/"]',
        "VALUE": "configured",
    }

    assert parse_bool(env, "BOOL", default=False) is True
    assert parse_int(env, "COUNT", default=1, minimum=1) == 2
    assert parse_runtime_duration("7d", "DURATION") == timedelta(days=7)
    assert parse_string_array(env, "ARRAY") == ("prefix/",)
    assert require_env(env, "VALUE") == "configured"
    assert optional_env(env, "VALUE") == "configured"


@pytest.mark.unit()
def test_parse_wrappers_raise_config_errors_for_invalid_values() -> None:
    with pytest.raises(ConfigError, match="BOOL must be true or false"):
        _ = parse_bool({"BOOL": "maybe"}, "BOOL", default=False)


@pytest.mark.unit()
def test_env_decoder_preserves_first_issue_when_fail_called_twice() -> None:
    decoder = EnvDecoder({})
    decoder.fail("FIRST", "first failure")
    decoder.fail("SECOND", "second failure")

    with pytest.raises(ConfigError, match="first failure"):
        decoder.finish()
