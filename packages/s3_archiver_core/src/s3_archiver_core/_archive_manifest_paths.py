from __future__ import annotations

from datetime import UTC, datetime


def normalize_prefix(value: str) -> str:
    stripped = value.strip("/")
    if stripped == "":
        return ""
    return f"{stripped}/"


def storage_identity(value: object | None) -> object | None:
    if value is None:
        return None
    storage_identity = getattr(value, "storage_identity", None)
    if callable(storage_identity):
        return storage_identity()
    return (type(value).__name__, getattr(value, "bucket", None))


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def relative_key(key: str, source_path: str) -> str:
    if source_path and key.startswith(source_path):
        return key[len(source_path) :]
    return key


def relative_archive_root(archive_root: str, source_path: str) -> str:
    prefix = source_path.rstrip("/")
    if prefix == "":
        return archive_root
    if archive_root == prefix:
        return ""
    child_prefix = f"{prefix}/"
    if archive_root.startswith(child_prefix):
        return archive_root[len(child_prefix) :]
    return archive_root


def route_path_prefix(path: str) -> str:
    normalized = normalize_prefix(path).rstrip("/")
    if normalized == "":
        return ""
    return f"{normalized}/"


def route_paths_overlap(left: str, right: str) -> bool:
    left_prefix = route_path_prefix(left)
    right_prefix = route_path_prefix(right)
    return left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)


def join_key(prefix: str, key: str) -> str:
    normalized_prefix = normalize_prefix(prefix)
    stripped_key = key.lstrip("/")
    return f"{normalized_prefix}{stripped_key}" if normalized_prefix else stripped_key
