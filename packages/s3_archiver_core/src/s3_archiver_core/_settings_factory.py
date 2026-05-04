from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Protocol

from s3_archiver_core._settings_models import (
    RouteSettings,
    S3LocationSettings,
)


class AppSettingsFactory[T](Protocol):
    """Callable constructor shape shared by settings decoders."""

    def __call__(
        self,
        *,
        source: S3LocationSettings,
        destination: S3LocationSettings,
        run_timeout: timedelta,
        temp_dir: Path,
        log_level: str,
        log_dir: Path,
        routes: tuple[RouteSettings, ...],
    ) -> T:
        """Build settings from decoded fields."""
        ...
