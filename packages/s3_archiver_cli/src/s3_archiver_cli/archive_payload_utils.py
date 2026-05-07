"""Generic JSON payload helper functions."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import cast

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]


def json_list(items: list[dict[str, JsonValue]]) -> list[JsonValue]:
    """Cast dictionaries into JSON-value lists for strict type checking."""

    return [cast(JsonValue, item) for item in items]


def attr(source: object, *names: str) -> object | None:
    """Read the first available attribute name from an object."""

    for name in names:
        if hasattr(source, name):
            return cast(object, getattr(source, name))
    return None


def object_list(value: object | None) -> list[object]:
    """Return iterable object values as a list, excluding strings."""

    if value is None or isinstance(value, str):
        return []
    if isinstance(value, Iterable):
        return list(value)
    return []


def count_from_attr(source: object, name: str, fallback_items: list[object]) -> int:
    """Return an integer count attribute or the fallback item count."""

    value = attr(source, name)
    return value if isinstance(value, int) else len(fallback_items)


def date_text(value: object) -> str:
    """Render dates and datetimes as ISO date strings."""

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def date_or_none(value: object | None) -> str | None:
    """Render date-like values only when present."""

    return None if value is None else date_text(value)


def datetime_text(value: object | None) -> str | None:
    """Render datetimes as ISO strings only when present."""

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def string_or_none(value: object) -> str | None:
    """Return a string value unless the input is None."""

    return None if value is None else str(value)


def int_or_none(value: object) -> int | None:
    """Return an integer value unless the input is not an integer."""

    return value if isinstance(value, int) else None
