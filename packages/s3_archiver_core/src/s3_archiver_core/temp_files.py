"""Runtime temp-file handling for archive transfers."""

from __future__ import annotations

import logging
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from s3_archiver_core.errors import ConfigError

TRANSFER_TEMP_PREFIX = "s3-archiver-transfer-"
_PRIVATE_TEMP_DIR_MODE = 0o700


def default_temp_dir() -> Path:
    """Return the dedicated default runtime temp directory."""

    return Path(tempfile.gettempdir()) / "s3-archiver"


def prepare_runtime_temp_dir(temp_dir: Path) -> None:
    """Create the runtime temp directory and remove stale archiver temp files."""

    ensure_runtime_temp_dir(temp_dir)
    _verify_transfer_temp_file_permissions(temp_dir)
    try:
        cleanup_stale_transfer_files(temp_dir)
    except OSError as exc:
        raise ConfigError(f"ARCHIVER_TEMP_DIR transfer cleanup failed: {exc}") from exc


def ensure_runtime_temp_dir(temp_dir: Path) -> None:
    """Create an owner-only runtime temp directory and verify its ownership."""

    try:
        temp_dir.mkdir(parents=True, mode=_PRIVATE_TEMP_DIR_MODE, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"ARCHIVER_TEMP_DIR cannot be created: {exc}") from exc
    if not temp_dir.is_dir():
        raise ConfigError("ARCHIVER_TEMP_DIR must be a directory")
    if temp_dir.is_symlink():
        raise ConfigError("ARCHIVER_TEMP_DIR must not be a symbolic link")
    try:
        metadata = temp_dir.stat()
    except OSError as exc:
        raise ConfigError(f"ARCHIVER_TEMP_DIR cannot be inspected: {exc}") from exc
    current_uid = os.geteuid()
    if metadata.st_uid != current_uid:
        raise ConfigError(
            "ARCHIVER_TEMP_DIR must be owned by the current user "
            + f"(expected uid {current_uid}, found uid {metadata.st_uid})"
        )
    try:
        temp_dir.chmod(_PRIVATE_TEMP_DIR_MODE)
        permissions = stat.S_IMODE(temp_dir.stat().st_mode)
    except OSError as exc:
        raise ConfigError(f"ARCHIVER_TEMP_DIR cannot be secured: {exc}") from exc
    if permissions != _PRIVATE_TEMP_DIR_MODE:
        raise ConfigError(
            "ARCHIVER_TEMP_DIR must have owner-only permissions "
            + f"(expected 0700, found {permissions:04o})"
        )


def _verify_transfer_temp_file_permissions(temp_dir: Path) -> None:
    probe_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", delete=False, dir=temp_dir, prefix=TRANSFER_TEMP_PREFIX
        ) as probe:
            probe_path = Path(probe.name)
            _ = probe.write(b"s3-archiver temp probe\n")
    except OSError as exc:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                raise ConfigError(
                    f"ARCHIVER_TEMP_DIR is not usable for transfer temp cleanup: {cleanup_exc}"
                ) from cleanup_exc
        raise ConfigError(
            f"ARCHIVER_TEMP_DIR is not usable for transfer temp files: {exc}"
        ) from exc
    try:
        probe_path.unlink()
    except OSError as exc:
        raise ConfigError(
            f"ARCHIVER_TEMP_DIR is not usable for transfer temp cleanup: {exc}"
        ) from exc


def cleanup_stale_transfer_files(temp_dir: Path) -> None:
    """Delete stale transfer files owned by this archiver."""

    for path in temp_dir.glob(f"{TRANSFER_TEMP_PREFIX}*"):
        if path.is_file():
            path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class TempStorageSnapshot:
    """Available storage for the runtime temp directory filesystem."""

    temp_dir: Path
    total_bytes: int
    used_bytes: int
    free_bytes: int


def log_temp_storage(temp_dir: Path) -> TempStorageSnapshot:
    """Log available runtime temp storage before archive work starts."""

    snapshot = temp_storage_snapshot(temp_dir)
    logging.getLogger("s3_archiver.archive").info(
        "archive temp storage available temp_dir=%s free_bytes=%d",
        snapshot.temp_dir,
        snapshot.free_bytes,
        extra={
            "event": "archive.temp_storage.available",
            "temp_dir": str(snapshot.temp_dir),
            "total_bytes": snapshot.total_bytes,
            "used_bytes": snapshot.used_bytes,
            "free_bytes": snapshot.free_bytes,
        },
    )
    return snapshot


def ensure_temp_storage_available(
    temp_dir: Path,
    *,
    required_bytes: int,
    source_key: str,
    destination_key: str,
    operation: str,
) -> TempStorageSnapshot:
    """Raise when a staged transfer cannot fit on the temp filesystem."""

    snapshot = temp_storage_snapshot(temp_dir)
    if snapshot.free_bytes >= required_bytes:
        return snapshot
    message = "".join(
        (
            f"{operation} requires {required_bytes} bytes in {temp_dir}, ",
            f"but only {snapshot.free_bytes} bytes are available ",
            f"(source_key={source_key}, destination_key={destination_key})",
        )
    )
    raise RuntimeError(message)


def temp_storage_snapshot(temp_dir: Path) -> TempStorageSnapshot:
    """Return disk usage for the filesystem backing the runtime temp directory."""

    temp_dir.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(temp_dir)
    return TempStorageSnapshot(
        temp_dir=temp_dir,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
    )
