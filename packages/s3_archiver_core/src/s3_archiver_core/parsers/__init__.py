"""Object parser package."""

from __future__ import annotations

from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.results import SelectedObject, SkippedObject, TimestampSource

__all__ = (
    "ParserKind",
    "SelectedObject",
    "SkippedObject",
    "TimestampSource",
)
