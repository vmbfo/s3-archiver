"""Shared field decoders for ARCHIVER_CONFIG_JSON route settings."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import cast
from urllib.parse import urlsplit

from s3_archiver_core._settings_models import S3AddressingStyle, S3LocationSettings, S3Provider
from s3_archiver_core._settings_parse import LOCALSTACK_ENDPOINT_HOSTS, EnvDecoder
from s3_archiver_core._settings_parse import normalize_endpoint_url_result as _endpoint_result

type JsonObject = dict[str, object]

_ENV_REF_RE = re.compile(r"\$\{(?P<key>[A-Z0-9_]+)\}")


def provider(decoder: EnvDecoder, value: str, field: str) -> S3Provider | None:
    valid = frozenset({item.value for item in S3Provider})
    normalized = value.lower()
    if normalized not in valid:
        decoder.fail(field, f"{field} must be one of {valid}, got {normalized!r}")
        return None
    return S3Provider(normalized)


def addressing_style(
    decoder: EnvDecoder, location: Mapping[str, object], field: str
) -> S3AddressingStyle | None:
    value = optional_string(decoder, location, "addressing_style", field, default="path")
    valid = frozenset({style.value for style in S3AddressingStyle})
    if value is None:
        return None
    normalized = value.lower()
    if normalized not in valid:
        decoder.fail(field, f"{field} must be one of {valid}, got {normalized!r}")
        return None
    return S3AddressingStyle(normalized)


def endpoint(decoder: EnvDecoder, location: Mapping[str, object], field: str) -> str | None:
    endpoint_url = optional_string(decoder, location, "endpoint_url", field)
    if endpoint_url is None:
        return None
    return decoder.consume(_endpoint_result(endpoint_url, field=field))


def object_config(decoder: EnvDecoder, item: object, field: str) -> JsonObject | None:
    if not isinstance(item, dict):
        decoder.fail(field, f"{field} must be a JSON object")
        return None
    raw = cast(Mapping[object, object], item)
    config: JsonObject = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            decoder.fail(field, f"{field} must contain only string keys")
            return None
        config[key] = value
    return config


def required_string(
    decoder: EnvDecoder, item: Mapping[str, object], key: str, field: str
) -> str | None:
    if key not in item:
        decoder.fail(field, f"{field} is required")
        return None
    value = item[key]
    if not isinstance(value, str):
        decoder.fail(field, f"{field} must be a string")
        return None
    expanded = expand_env_refs(decoder, value, field).strip()
    if expanded == "":
        decoder.fail(field, f"{field} is required")
        return None
    return expanded


def optional_string(
    decoder: EnvDecoder,
    item: Mapping[str, object],
    key: str,
    field: str,
    *,
    default: str | None = None,
) -> str | None:
    if key not in item or item[key] is None:
        return default
    value = item[key]
    if not isinstance(value, str):
        decoder.fail(field, f"{field} must be a string")
        return None
    return expand_env_refs(decoder, value, field).strip()


def expand_env_refs(decoder: EnvDecoder, value: str, field: str) -> str:
    expanded = value
    for match in _ENV_REF_RE.finditer(value):
        env_key = match["key"]
        replacement = decoder.env.get(env_key)
        if replacement is None:
            decoder.fail(field, f"{field} references missing environment variable {env_key}")
            return value
        expanded = expanded.replace(match.group(0), replacement)
    return expanded


def normalize_s3_prefix(path: str) -> str:
    return path.strip().lstrip("/")


def validate_localstack_endpoint_host(
    decoder: EnvDecoder, field: str, location: S3LocationSettings
) -> None:
    if location.provider is not S3Provider.LOCALSTACK:
        return
    host = urlsplit(location.resolved_endpoint_url()).hostname
    if host not in LOCALSTACK_ENDPOINT_HOSTS:
        decoder.fail(field, f"{field} host {host!r} is not allowed when provider=localstack")
