"""Run lock and timeout primitives for archive invocations."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast


class FileArchiveRunLock:
    """File-backed run lock with timeout-based stale lock recovery."""

    _path: Path

    def __init__(self, path: Path) -> None:
        self._path = path

    def acquire(self, *, run_id: str, run_started_at_utc: datetime, timeout: timedelta) -> bool:
        """Acquire the file lock unless a non-stale run owns it."""

        if self._path.exists() and not self._existing_lock_is_stale(timeout):
            return False
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _ = self._path.write_text(
            json.dumps(
                {"run_id": run_id, "run_started_at_utc": run_started_at_utc.isoformat()},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return True

    def release(self, *, run_id: str) -> None:
        """Release the lock only when the expected run owns it."""

        if not self._path.exists():
            return
        if _lock_run_id(self._path) == run_id:
            self._path.unlink()

    def _existing_lock_is_stale(self, timeout: timedelta) -> bool:
        started = _lock_started_at(self._path)
        if started is None:
            self._path.unlink()
            return True
        stale = datetime.now(tz=UTC) - started > timeout
        if stale:
            self._path.unlink()
        return stale


def parse_duration(value: str) -> timedelta:
    """Parse archive durations like ``7d``, ``12h``, or ``30m``."""

    if len(value) < 2:
        raise ValueError(f"invalid duration {value!r}")
    amount = int(value[:-1])
    unit = value[-1]
    if amount <= 0:
        raise ValueError(f"invalid duration {value!r}")
    if unit == "d":
        return timedelta(days=amount)
    if unit == "h":
        return timedelta(hours=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    raise ValueError(f"invalid duration {value!r}")


def _lock_started_at(path: Path) -> datetime | None:
    decoded = _lock_json(path)
    value = decoded.get("run_started_at_utc")
    if not isinstance(value, str):
        return None
    return datetime.fromisoformat(value)


def _lock_run_id(path: Path) -> str | None:
    value = _lock_json(path).get("run_id")
    if isinstance(value, str):
        return value
    return None


def _lock_json(path: Path) -> Mapping[str, object]:
    try:
        decoded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(decoded, dict):
        return cast(Mapping[str, object], decoded)
    return {}
