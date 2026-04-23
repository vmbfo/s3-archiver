"""Run lock and timeout primitives for archive invocations."""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

LockRecoveryLogger = Callable[[str, Mapping[str, object]], None]


class FileArchiveRunLock:
    """File-backed run lock with timeout-based stale lock recovery."""

    _path: Path
    _recovery_logger: LockRecoveryLogger | None

    def __init__(self, path: Path, recovery_logger: LockRecoveryLogger | None = None) -> None:
        self._path = path
        self._recovery_logger = recovery_logger

    def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: timedelta) -> bool:
        """Acquire the file lock unless a non-stale run owns it."""

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "hostname": socket.gethostname(),
                "pid": os.getpid(),
                "run_id": run_id,
                "run_started_at_utc": run_started_at_utc.isoformat(),
            },
            sort_keys=True,
        )
        for _attempt in range(2):
            try:
                descriptor = os.open(
                    self._path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if not self._existing_lock_is_stale(timeout):
                    return False
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
                _ = lock_file.write(payload)
            return True
        return False

    def release(self, *, run_id: str) -> None:
        """Release the lock only when the expected run owns it."""

        if not self._path.exists():
            return
        if _lock_run_id(self._path) == run_id:
            _safe_unlink(self._path)

    def _existing_lock_is_stale(self, timeout: timedelta) -> bool:
        decoded = _lock_json(self._path)
        started = _lock_started_at(decoded)
        if started is None:
            self._log_recovery("invalid_lock_metadata", decoded)
            _safe_unlink(self._path)
            return True
        timed_out = datetime.now(tz=UTC) - started > timeout
        abandoned = not _lock_process_is_alive_on_this_host(decoded)
        if not timed_out and not abandoned:
            return False
        reason = "stale_lock_timed_out" if timed_out else "stale_lock_abandoned"
        self._log_recovery(reason, decoded)
        _safe_unlink(self._path)
        return True

    def _log_recovery(self, reason: str, payload: Mapping[str, object]) -> None:
        if self._recovery_logger is not None:
            self._recovery_logger(reason, payload)


def parse_duration(value: str) -> timedelta:
    """Parse archive durations like ``7d``, ``12h``, ``30m``, or ``45s``."""

    if len(value) < 2:
        raise ValueError(f"invalid duration {value!r}")
    stripped = value.strip().lower()
    amount = int(stripped[:-1])
    unit = stripped[-1]
    if amount <= 0:
        raise ValueError(f"invalid duration {value!r}")
    if unit == "d":
        return timedelta(days=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "s":
        return timedelta(seconds=amount)
    raise ValueError(f"invalid duration {value!r}")


def _lock_started_at(decoded: Mapping[str, object]) -> datetime | None:
    value = decoded.get("run_started_at_utc")
    if not isinstance(value, str):
        return None
    try:
        started = datetime.fromisoformat(value)
    except ValueError:
        return None
    if started.tzinfo is None or started.utcoffset() is None:
        return None
    return started.astimezone(UTC)


def _lock_run_id(path: Path) -> str | None:
    value = _lock_json(path).get("run_id")
    if isinstance(value, str):
        return value
    return None


def _lock_process_is_alive_on_this_host(decoded: Mapping[str, object]) -> bool:
    hostname = decoded.get("hostname")
    pid = decoded.get("pid")
    if hostname != socket.gethostname() or type(pid) is not int or pid <= 0:
        return False
    return _process_is_alive(pid)


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_json(path: Path) -> Mapping[str, object]:
    try:
        decoded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(decoded, dict):
        return cast(Mapping[str, object], decoded)
    return {}


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
