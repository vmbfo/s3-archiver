"""Runtime temp-file handling for archive transfers."""

from __future__ import annotations

import tempfile
from pathlib import Path

from s3_archiver_core.errors import ConfigError

TRANSFER_TEMP_PREFIX = "s3-archiver-transfer-"


def default_temp_dir() -> Path:
    """Return the dedicated default runtime temp directory."""

    return Path(tempfile.gettempdir()) / "s3-archiver"


def prepare_runtime_temp_dir(temp_dir: Path) -> None:
    """Create the runtime temp directory and remove stale archiver temp files."""

    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"ARCHIVER_TEMP_DIR cannot be created: {exc}") from exc
    if not temp_dir.is_dir():
        raise ConfigError("ARCHIVER_TEMP_DIR must be a directory")
    cleanup_stale_transfer_files(temp_dir)


def cleanup_stale_transfer_files(temp_dir: Path) -> None:
    """Delete stale transfer files owned by this archiver."""

    for path in temp_dir.glob(f"{TRANSFER_TEMP_PREFIX}*"):
        if path.is_file():
            path.unlink(missing_ok=True)
