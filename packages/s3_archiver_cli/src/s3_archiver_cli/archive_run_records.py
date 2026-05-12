"""Durable archive run state records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from s3_archiver_core.archive import ArchiveRunResult
from s3_archiver_core.settings import AppSettings

from s3_archiver_cli.archive_paths import archive_lock_path
from s3_archiver_cli.archive_payload_utils import JsonValue
from s3_archiver_cli.route_payloads import route_summary_payload as _route_summary_payload


def record_started(
    settings: AppSettings,
    *,
    run_id: str,
    run_started_at_utc: datetime,
    log_file: Path,
) -> None:
    """Persist an active run record after lock acquisition."""

    _write_record(
        settings,
        run_id,
        _record_payload(
            settings,
            "active",
            run_id=run_id,
            run_started_at_utc=run_started_at_utc,
            log_file=log_file,
        ),
    )


def record_result(
    settings: AppSettings,
    *,
    result: ArchiveRunResult,
    payload: Mapping[str, JsonValue],
    log_file: Path,
) -> None:
    """Persist a terminal archive run record."""

    status = "succeeded" if payload.get("status") == "ok" else "failed"
    _write_record(
        settings,
        result.run_id,
        _record_payload(
            settings,
            status,
            run_id=result.run_id,
            run_started_at_utc=result.manifest.run_started_at_utc,
            log_file=log_file,
            payload=payload,
        ),
    )


def record_failure(
    settings: AppSettings,
    *,
    run_id: str,
    run_started_at_utc: datetime,
    payload: Mapping[str, JsonValue],
    log_file: Path,
) -> None:
    """Persist a terminal failed run record for setup/runtime exceptions."""

    _write_record(
        settings,
        run_id,
        _record_payload(
            settings,
            "failed",
            run_id=run_id,
            run_started_at_utc=run_started_at_utc,
            log_file=log_file,
            payload=payload,
        ),
    )


def record_subprocess_timeout(
    settings: AppSettings,
    *,
    payload: Mapping[str, JsonValue],
    log_file: Path,
    lock_payload: Mapping[str, object] | None = None,
) -> None:
    """Persist a failed run record when the parent times out a child process."""

    lock = (
        lock_payload if lock_payload is not None else read_lock_payload(archive_lock_path(settings))
    )
    run_id = _string(lock.get("run_id")) or f"unknown-{datetime.now(tz=UTC).timestamp():.0f}"
    started = _string(lock.get("run_started_at_utc"))
    _write_record(
        settings,
        run_id,
        _record_payload(
            settings,
            "failed",
            run_id=run_id,
            run_started_at_utc=started,
            log_file=log_file,
            payload=payload,
        ),
    )


def _write_record(settings: AppSettings, run_id: str, payload: Mapping[str, JsonValue]) -> None:
    directory = settings.log_dir / "archive-runs"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.json"
    _ = path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _record_payload(
    settings: AppSettings,
    status: str,
    *,
    run_id: str,
    run_started_at_utc: datetime | str | None,
    log_file: Path,
    payload: Mapping[str, JsonValue] | None = None,
) -> dict[str, JsonValue]:
    record: dict[str, JsonValue] = {
        "schema_version": 1,
        "status": status,
        "run_id": run_id,
        "run_started_at_utc": _timestamp(run_started_at_utc),
        "updated_at_utc": _now(),
        **_route_summary_payload(settings),
        "log_file": str(log_file),
    }
    if payload is not None:
        record["payload"] = dict(payload)
    return record


def read_lock_payload(path: Path) -> Mapping[str, object]:
    """Read the current archive lock payload, returning an empty mapping when unavailable."""

    try:
        decoded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return cast(Mapping[str, object], decoded) if isinstance(decoded, dict) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _timestamp(value: datetime | str | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()
