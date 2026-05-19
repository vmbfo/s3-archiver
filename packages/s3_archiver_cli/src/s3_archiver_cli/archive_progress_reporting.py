"""Archive progress logging helpers."""

from __future__ import annotations

import logging
import os
import time
from threading import Lock
from typing import final

from s3_archiver_core.archive_progress import ArchiveProgress


@final
class ArchiveProgressReporter:
    """Emit archive progress log events once per integer percent."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("s3_archiver.archive")
        self._started_by_phase: dict[str, float] = {}
        self._last_percent_by_phase: dict[str, int] = {}
        self._last_completed_by_phase: dict[str, int] = {}
        self._lock = Lock()

    def __call__(self, progress: ArchiveProgress) -> None:
        with self._lock:
            percent = progress.percent
            if not self._should_log(progress, percent):
                return
            now = time.monotonic()
            started = self._started_by_phase.setdefault(progress.phase, now)
            elapsed_seconds = max(now - started, 0.0)
            eta_seconds = _eta_seconds(progress, elapsed_seconds)
            progress_bar = _progress_bar(percent)
        _ = self._logger.info(
            "archive progress %s %d%% %s eta=%s",
            progress.phase,
            percent,
            progress_bar,
            _format_duration(eta_seconds),
            extra={
                "event": "archive.progress",
                "phase": progress.phase,
                "completed_units": progress.completed,
                "total_units": progress.total,
                "percent": percent,
                "progress_bar": progress_bar,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "eta_seconds": eta_seconds,
                "eta": _format_duration(eta_seconds),
            },
        )

    def _should_log(self, progress: ArchiveProgress, percent: int) -> bool:
        last_percent = self._last_percent_by_phase.get(progress.phase)
        last_completed = self._last_completed_by_phase.get(progress.phase, 0)
        if last_percent is not None and percent <= last_percent:
            if progress.completed < last_completed + _progress_step(progress):
                return False
            self._last_completed_by_phase[progress.phase] = progress.completed
            self._last_percent_by_phase[progress.phase] = percent
            return True
        self._last_completed_by_phase[progress.phase] = progress.completed
        self._last_percent_by_phase[progress.phase] = percent
        return True


def _progress_step(progress: ArchiveProgress) -> int:
    if progress.total <= 0:
        return 1000
    return max(progress.total // 100, 1)


def include_archive_payload_details() -> bool:
    """Return whether archive result payloads should include per-object detail."""

    value = os.environ.get("ARCHIVER_PAYLOAD_DETAIL", "summary").strip().lower()
    return value in {"1", "true", "full", "detailed"}


def _eta_seconds(progress: ArchiveProgress, elapsed_seconds: float) -> int | None:
    if progress.total <= 0 or progress.completed >= progress.total:
        return 0
    if progress.completed <= 0 or elapsed_seconds <= 0:
        return None
    rate = progress.completed / elapsed_seconds
    return max(int((progress.total - progress.completed) / rate), 0)


def _progress_bar(percent: int) -> str:
    width = 20
    filled = min(max((percent * width) // 100, 0), width)
    return f"[{'#' * filled}{'.' * (width - filled)}]"


def _format_duration(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"
