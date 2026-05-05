"""Compatibility exports for timestamp parser helpers."""

from __future__ import annotations

from s3_archiver_core.parsers.filename_timestamp import (
    archive_root_for_key,
    destination_archive_key,
    select_folder_timestamp,
    select_key_timestamp,
)
from s3_archiver_core.parsers.results import TimestampSource

__all__ = (
    "TimestampSource",
    "archive_root_for_key",
    "destination_archive_key",
    "select_folder_timestamp",
    "select_key_timestamp",
)
