from __future__ import annotations

from threading import Lock
from typing import final

from s3_archiver_core.archive_progress import ArchiveProgress, ProgressLogger


@final
class PhaseProgress:
    """Thread-safe progress counter for one archive phase."""

    def __init__(
        self,
        phase: str,
        total: int,
        progress_logger: ProgressLogger | None,
    ) -> None:
        self._phase = phase
        self._total = total
        self._progress_logger = progress_logger
        self._completed = 0
        self._lock = Lock()
        if progress_logger is not None and total == 0:
            progress_logger(ArchiveProgress(phase, 0, 0))

    def advance(self, count: int = 1) -> None:
        if count <= 0 or self._progress_logger is None:
            return
        with self._lock:
            self._completed = min(self._completed + count, self._total)
            progress = ArchiveProgress(self._phase, self._completed, self._total)
        self._progress_logger(progress)
