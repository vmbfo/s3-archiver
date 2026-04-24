"""Tests for stale archive-lock recovery reporting helpers."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import pytest
import s3_archiver_cli.archive_lock_reporting as lock_reporting
import typer


@pytest.mark.unit()
def test_recovered_run_failure_payload_ignores_unknown_reason() -> None:
    assert lock_reporting.recovered_run_failure_payload("other", {}) is None


@pytest.mark.unit()
def test_archive_lock_recovery_logger_skips_payload_for_unknown_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[str] = []

    def capture_echo(message: str, *, err: bool = False, nl: bool = True) -> None:
        _ = (err, nl)
        emitted.append(message)

    monkeypatch.setattr(typer, "echo", capture_echo)

    lock_reporting.log_lock_recovery("other", {"run_id": "run-1"})

    assert emitted == []


@pytest.mark.unit()
def test_archive_lock_recovery_logger_coerces_non_scalar_payload_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[str] = []
    logged_payloads: list[Mapping[str, object]] = []

    def capture_echo(message: str, *, err: bool = False, nl: bool = True) -> None:
        _ = (err, nl)
        emitted.append(message)

    def capture_error_payload(
        payload: Mapping[str, object],
        _error: BaseException | None = None,
    ) -> None:
        logged_payloads.append(payload)

    monkeypatch.setattr(typer, "echo", capture_echo)
    monkeypatch.setattr(
        lock_reporting,
        "_log_error_payload",
        capture_error_payload,
    )

    lock_reporting.log_lock_recovery(
        "stale_lock_abandoned",
        {"run_id": ["not-scalar"], "hostname": {"nested": "value"}},
    )

    payload = cast(dict[str, object], json.loads(emitted[-1]))
    assert payload["run_id"] is None
    assert payload["hostname"] is None
    assert logged_payloads[-1]["recovered"] is True
