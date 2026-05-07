"""Parser result models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

TimestampSource = Literal["last_modified", "basename", "path"]


@dataclass(frozen=True, slots=True)
class SelectedObject:
    """Source object selected by a parser."""

    timestamp: datetime
    timestamp_source: TimestampSource
    archive_root: str


@dataclass(frozen=True, slots=True)
class SkippedObject:
    """Source object skipped by a parser."""

    reason: str
