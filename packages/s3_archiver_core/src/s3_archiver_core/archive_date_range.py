"""Day-level archive date-range bounds.

An :class:`ArchiveDateRange` restricts an archive run to objects whose
parser-selected date falls within an inclusive ``[start, end]`` window. Bounds
are configured with ``ARCHIVER_FROM`` / ``ARCHIVER_TO`` and may be given at any
granularity: a year (``2019``), year-month (``2019-01``), day (``2019-01-01``),
or a timestamp (``2019-01-01T10:00:00``). Matching is day-level: a ``from`` bound
expands to the first day of its period, a ``to`` bound to the last day, and a
timestamp bound is rounded down to its day. Either bound may be omitted for an
open-ended range; an empty range includes every day.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from typing import Literal

__all__ = ("NO_DATE_RANGE", "ArchiveDateRange", "parse_date_bound")

Bound = Literal["start", "end"]


@dataclass(frozen=True, slots=True)
class ArchiveDateRange:
    """Inclusive day-level window restricting which objects an archive run selects."""

    start: date | None = None
    end: date | None = None

    def includes(self, day: date) -> bool:
        """Return whether ``day`` falls within the configured bounds."""

        if self.start is not None and day < self.start:
            return False
        return self.end is None or day <= self.end


NO_DATE_RANGE = ArchiveDateRange()
"""Shared open range used as the default when no window is configured."""


def parse_date_bound(text: str, *, bound: Bound) -> date:
    """Parse one range bound into the inclusive day it represents.

    ``bound="start"`` expands a partial value to the first day of its period;
    ``bound="end"`` expands it to the last day. Raises :class:`ValueError` for
    values that are not a year, year-month, day, or day-with-time.
    """

    date_part = _split_date_part(text.strip())
    parts = date_part.split("-")
    if len(parts) == 1:
        year = _component(parts[0], field="year", width=4)
        return date(year, 1, 1) if bound == "start" else date(year, 12, 31)
    if len(parts) == 2:
        year = _component(parts[0], field="year", width=4)
        month = _component(parts[1], field="month", width=2)
        day = 1 if bound == "start" else _last_day_of_month(year, month)
        return _build_date(year, month, day, text)
    if len(parts) == 3:
        year = _component(parts[0], field="year", width=4)
        month = _component(parts[1], field="month", width=2)
        day = _component(parts[2], field="day", width=2)
        return _build_date(year, month, day, text)
    raise ValueError(f"date bound {text!r} must be a year, year-month, day, or timestamp")


def _split_date_part(value: str) -> str:
    if value == "":
        raise ValueError("date bound must not be empty")
    for separator in ("T", " "):
        if separator in value:
            return value.split(separator, maxsplit=1)[0]
    return value


def _component(value: str, *, field: str, width: int) -> int:
    if len(value) != width or not value.isdigit():
        raise ValueError(f"date bound {field} {value!r} must be {width} digits")
    return int(value)


def _last_day_of_month(year: int, month: int) -> int:
    _reject_invalid_month(month)
    return calendar.monthrange(year, month)[1]


def _build_date(year: int, month: int, day: int, text: str) -> date:
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise ValueError(f"date bound {text!r} is not a valid date: {exc}") from exc


def _reject_invalid_month(month: int) -> None:
    if not 1 <= month <= 12:
        raise ValueError(f"date bound month {month!r} must be between 01 and 12")
