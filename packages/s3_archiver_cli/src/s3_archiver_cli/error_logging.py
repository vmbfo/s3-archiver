"""Structured CLI error logging helpers."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]


def log_error_payload(payload: Mapping[str, JsonValue], error: Exception | None = None) -> None:
    """Log a structured error payload after logging is configured."""

    if payload.get("phase") == "startup.env_validation":
        return
    logger = logging.getLogger("s3_archiver.archive")
    log_method = logger.exception if error is not None else logger.error
    log_method(
        str(payload.get("message", "s3 archiver error")),
        extra=_error_log_extra(payload),
    )


def _error_log_extra(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    extra: dict[str, JsonValue] = {
        "event": "s3_archiver.error",
        "error_payload_json": json.dumps(payload, sort_keys=True),
    }
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            extra[f"error_{key}"] = value
    return extra
