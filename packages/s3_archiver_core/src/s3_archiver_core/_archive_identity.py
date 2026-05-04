"""Stable identity serialization for persisted archive metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import is_dataclass
from enum import Enum
from typing import cast


def stable_identity_value(value: object) -> object:
    """Return a JSON-compatible, deterministic representation of an identity."""

    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Enum):
        return value.name
    if is_dataclass(value) and not isinstance(value, type):
        return repr(value)
    if isinstance(value, Mapping):
        mapping = cast(Mapping[object, object], value)
        return {
            str(key): stable_identity_value(item)
            for key, item in sorted(mapping.items(), key=_mapping_key)
        }
    if isinstance(value, tuple | list):
        sequence = cast(Sequence[object], value)
        return [stable_identity_value(item) for item in sequence]
    return repr(value)


def _mapping_key(item: tuple[object, object]) -> str:
    return str(item[0])
