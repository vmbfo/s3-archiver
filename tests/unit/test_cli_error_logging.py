"""Unit tests for CLI error logging helpers."""

from __future__ import annotations

import logging

import pytest
from s3_archiver_cli.error_logging import log_error_payload


@pytest.mark.unit()
def test_log_error_payload_skips_logging_without_root_archiver_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logger = logging.getLogger("s3_archiver")
    archive_logger = logging.getLogger("s3_archiver.archive")
    monkeypatch.setattr(logger, "handlers", [])

    def fail_error(*args: object, **kwargs: object) -> None:
        raise AssertionError(f"unexpected archive logger call: {args}, {kwargs}")

    monkeypatch.setattr(archive_logger, "error", fail_error)
    monkeypatch.setattr(archive_logger, "exception", fail_error)

    log_error_payload({"message": "archive run failed"})
