"""Private parsing helpers for settings."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import timedelta
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from s3_archiver_core.archive_lock import parse_duration
from s3_archiver_core.errors import ConfigError


def parse_bool(env: Mapping[str, str], key: str, *, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigError(f"{key} must be true or false")


def parse_int(env: Mapping[str, str], key: str, *, default: int, minimum: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if value < minimum:
        raise ConfigError(f"{key} must be greater than or equal to {minimum}")
    return value


def parse_runtime_duration(raw: str, key: str) -> timedelta:
    try:
        return parse_duration(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a positive duration such as 7d") from exc


def parse_string_array(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    raw = env.get(key, "[]")
    if raw.strip() == "":
        raw = "[]"
    try:
        parsed = cast(object, json.loads(raw))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{key} must be a JSON array of strings") from exc
    if not isinstance(parsed, list):
        raise ConfigError(f"{key} must be a JSON array of strings")
    items: list[str] = []
    for item in cast(list[object], parsed):
        if not isinstance(item, str):
            raise ConfigError(f"{key} must be a JSON array of strings")
        items.append(item)
    return tuple(items)


def normalize_endpoint_url(raw: str, *, field: str = "S3_ENDPOINT_URL") -> str:
    parsed = urlsplit(raw)
    if parsed.scheme == "" or parsed.hostname is None:
        raise ConfigError(f"{field} must include a URL scheme and host, got {raw!r}")
    if parsed.query != "" or parsed.fragment != "":
        raise ConfigError(f"{field} must not include query or fragment components")
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ConfigError(f"{field} scheme must be http or https, got {scheme!r}")
    hostname = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise ConfigError(f"{field} has an invalid port") from exc
    default_port = 80 if scheme == "http" else 443
    netloc = hostname if port in {None, default_port} else f"{hostname}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def require_env(env: Mapping[str, str], key: str) -> str:
    value = env.get(key)
    if value is None or value.strip() == "":
        raise ConfigError(f"{key} is required")
    return value.strip()


def optional_env(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None or value.strip() == "":
        return None
    return value.strip()
