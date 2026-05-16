"""Archive progress callback models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArchiveProgress:
    """One archive progress update."""

    phase: str
    completed: int
    total: int

    @property
    def percent(self) -> int:
        """Return completed progress as a floored integer percent."""

        if self.total <= 0:
            return 100
        return min((self.completed * 100) // self.total, 100)


type ProgressLogger = Callable[[ArchiveProgress], None]
