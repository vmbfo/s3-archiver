"""Shared CLI archive path helpers."""

from __future__ import annotations

from pathlib import Path

from s3_archiver_core.settings import AppSettings


def archive_lock_path(settings: AppSettings) -> Path:
    """Return the path to the archive run lock file."""

    return settings.log_dir / "archive.lock"
