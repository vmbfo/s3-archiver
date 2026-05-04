"""Edge-case tests for settings parsing helpers."""

from __future__ import annotations

from datetime import timedelta

import pytest
from s3_archiver_core._route_config_fields import (
    addressing_style,
    object_config,
    optional_string,
    required_string,
)
from s3_archiver_core._settings_parse import (
    EnvDecoder,
    ParseIssue,
    ParseResult,
    normalize_endpoint_url_result,
    optional_env,
    optional_env_result,
    parse_bool,
    parse_bool_result,
    parse_int,
    parse_int_result,
    parse_runtime_duration,
    parse_runtime_duration_result,
    parse_string_array,
    parse_string_array_result,
    require_env,
    require_env_result,
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
def test_default_endpoint_resolution_covers_supported_providers() -> None:
    localstack = S3LocationSettings(
        provider=S3Provider.LOCALSTACK,
        access_key_id="access",
        secret_access_key="secret",
        region="us-east-1",
        bucket="bucket",
        namespace=None,
        iam_user_ocid=None,
        endpoint_url=None,
        addressing_style=S3AddressingStyle.PATH,
    )
    oci = S3LocationSettings(
        provider=S3Provider.OCI,
        access_key_id="access",
        secret_access_key="secret",
        region="eu-frankfurt-1",
        bucket="bucket",
        namespace="namespace",
        iam_user_ocid="ocid1.user.oc1..example",
        endpoint_url=None,
        addressing_style=S3AddressingStyle.PATH,
    )

    assert localstack.resolved_endpoint_url() == "http://localstack:4566"
    assert (
        oci.resolved_endpoint_url()
        == "https://namespace.compat.objectstorage.eu-frankfurt-1.oraclecloud.com"
    )


@pytest.mark.unit()
def test_parse_result_boundary_captures_issue_without_raising() -> None:
    result = parse_bool_result({"BOOL": "maybe"}, "BOOL", default=False)

    assert result.ok is False
    assert result.value is None
    assert result.issue is not None
    assert result.issue.field == "BOOL"


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
def test_parse_result_helpers_cover_error_edges() -> None:
    assert parse_bool_result({"BOOL": "false"}, "BOOL", default=True).value is False
    assert parse_bool_result({}, "BOOL", default=True).value is True
    assert parse_int_result({"COUNT": " "}, "COUNT", default=1, minimum=1).value == 1
    assert parse_int_result({"COUNT": "no"}, "COUNT", default=1, minimum=1).issue is not None
    assert parse_int_result({"COUNT": "0"}, "COUNT", default=1, minimum=1).issue is not None
    assert parse_runtime_duration_result("soon", "DURATION").issue is not None
    assert parse_string_array_result({"ARRAY": ""}, "ARRAY").value == ()
    assert parse_string_array_result({"ARRAY": "["}, "ARRAY").issue is not None
    assert parse_string_array_result({"ARRAY": "{}"}, "ARRAY").issue is not None
    assert parse_string_array_result({"ARRAY": "[1]"}, "ARRAY").issue is not None
    assert normalize_endpoint_url_result("localhost:4566", field="ENDPOINT").issue is not None
    assert normalize_endpoint_url_result("http://host/path?x=1", field="ENDPOINT").issue is not None
    assert normalize_endpoint_url_result("ftp://host", field="ENDPOINT").issue is not None
    assert normalize_endpoint_url_result("http://host:bad", field="ENDPOINT").issue is not None
    assert require_env_result({}, "MISSING").issue is not None
    assert optional_env_result({"OPTIONAL": " "}, "OPTIONAL").value is None


@pytest.mark.unit()
def test_route_config_field_helpers_cover_invalid_shapes() -> None:
    decoder = EnvDecoder({})
    assert object_config(decoder, [], "ROUTE") is None

    decoder = EnvDecoder({})
    assert object_config(decoder, {1: "value"}, "ROUTE") is None

    decoder = EnvDecoder({})
    assert required_string(decoder, {}, "name", "ROUTE.name") is None

    decoder = EnvDecoder({})
    assert required_string(decoder, {"name": 3}, "name", "ROUTE.name") is None

    decoder = EnvDecoder({})
    assert required_string(decoder, {"name": " "}, "name", "ROUTE.name") is None

    decoder = EnvDecoder({})
    assert optional_string(decoder, {"path": 3}, "path", "ROUTE.path") is None

    decoder = EnvDecoder({})
    assert optional_string(decoder, {"path": "${MISSING}"}, "path", "ROUTE.path") == "${MISSING}"

    decoder = EnvDecoder({})
    assert addressing_style(decoder, {"addressing_style": 3}, "ROUTE.addressing_style") is None

    decoder = EnvDecoder({})
    assert addressing_style(decoder, {"addressing_style": "bad"}, "ROUTE.addressing_style") is None


@pytest.mark.unit()
def test_env_decoder_preserves_first_issue_when_fail_called_twice() -> None:
    decoder = EnvDecoder({})
    decoder.fail("FIRST", "first failure")
    decoder.fail("SECOND", "second failure")

    with pytest.raises(ConfigError, match="first failure"):
        decoder.finish()


@pytest.mark.unit()
def test_env_decoder_consume_captures_first_parse_issue() -> None:
    decoder = EnvDecoder({})

    assert decoder.consume(ParseResult[str](None, ParseIssue("FIELD", "field failed"))) is None

    with pytest.raises(ConfigError, match="field failed"):
        decoder.finish()
