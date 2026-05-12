"""CLI helpers for stale archive-lock recovery reporting."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

import typer
from s3_archiver_core.payload_utils import JsonScalar, JsonValue

from s3_archiver_cli.error_logging import log_error_payload as _log_error_payload


def log_lock_recovery(reason: str, payload: Mapping[str, object]) -> None:
    """Emit stale-lock recovery diagnostics to logs and stderr JSON."""

    logger = logging.getLogger("s3_archiver.archive")
    logger.warning(
        "archive stale run lock recovered",
        extra={
            "event": "archive.lock.recovered",
            "reason": reason,
            "stale_run_id": payload.get("run_id"),
            "stale_run_started_at_utc": payload.get("run_started_at_utc"),
            "stale_hostname": payload.get("hostname"),
            "stale_pid": payload.get("pid"),
        },
    )
    failure_payload = recovered_run_failure_payload(reason, payload)
    if failure_payload is not None:
        _log_error_payload(failure_payload)
        typer.echo(json.dumps(failure_payload, sort_keys=True), err=True)


def recovered_run_failure_payload(
    reason: str,
    payload: Mapping[str, object],
) -> dict[str, JsonValue] | None:
    """Build a portable error payload for a recovered stale lock."""

    timed_out = reason == "stale_lock_timed_out"
    if reason not in {"stale_lock_abandoned", "stale_lock_timed_out"}:
        return None
    return {
        "status": "error",
        "phase": "archive.run",
        "field": "ARCHIVER_RUN_TIMEOUT" if timed_out else None,
        "message": "prior archive run failed and was recovered from a stale lock",
        "details": "archive run timed out" if timed_out else "archive run was abandoned",
        "source_bucket": None,
        "destination_bucket": None,
        "key": None,
        "mismatch": None,
        "reason": "archive_run_timeout" if timed_out else "archive_run_abandoned",
        "timed_out": timed_out,
        "run_id": _json_scalar(payload.get("run_id")),
        "run_started_at_utc": _json_scalar(payload.get("run_started_at_utc")),
        "hostname": _json_scalar(payload.get("hostname")),
        "pid": _json_scalar(payload.get("pid")),
        "lock_recovery_reason": reason,
        "recovered": True,
    }


def _json_scalar(value: object) -> JsonScalar:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None
