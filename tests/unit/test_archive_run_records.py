"""Tests for durable archive run records."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from pathlib import Path
from typing import cast

import pytest
import s3_archiver_cli.scheduled_archive as scheduled_archive_module
from s3_archiver_cli import archive_run_records
from s3_archiver_core.settings import AppSettings


@pytest.mark.unit()
def test_archive_subprocess_timeout_records_child_lock_before_recovery(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    settings = AppSettings.from_env(base_env)
    lock_path = Path(base_env["LOG_DIR"]) / "archive.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    child_started = "2000-01-01T00:00:00+00:00"

    def fake_run_command(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        _ = lock_path.write_text(
            json.dumps(
                {
                    "hostname": socket.gethostname(),
                    "pid": 999_999_999,
                    "run_id": "child-run",
                    "run_started_at_utc": child_started,
                }
            ),
            encoding="utf-8",
        )
        raise subprocess.TimeoutExpired(command, timeout=1, output=None, stderr=None)

    exit_code = scheduled_archive_module.run_archive_subprocess(
        settings,
        Path("/tmp/log"),
        command=["archive"],
        run_command=fake_run_command,
        stderr_echo=lambda _message: None,
        log_error=lambda _payload: None,
    )

    assert exit_code == 1
    assert not lock_path.exists()
    record = _record(Path(base_env["LOG_DIR"]) / "archive-runs" / "child-run.json")
    assert record["status"] == "failed"
    assert record["run_id"] == "child-run"
    assert record["run_started_at_utc"] == child_started


@pytest.mark.unit()
def test_record_subprocess_timeout_falls_back_when_lock_payload_is_missing(
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)

    archive_run_records.record_subprocess_timeout(
        settings,
        payload={"status": "error", "reason": "archive_run_timeout"},
        log_file=Path("/tmp/log"),
        lock_payload={},
    )

    records = sorted((Path(base_env["LOG_DIR"]) / "archive-runs").glob("unknown-*.json"))
    assert len(records) == 1
    record = _record(records[0])
    assert record["status"] == "failed"
    assert record["run_started_at_utc"] is None


@pytest.mark.unit()
def test_read_lock_payload_handles_valid_invalid_and_missing_files(tmp_path: Path) -> None:
    lock_path = tmp_path / "archive.lock"

    assert archive_run_records.read_lock_payload(lock_path) == {}
    _ = lock_path.write_text("not json", encoding="utf-8")
    assert archive_run_records.read_lock_payload(lock_path) == {}
    _ = lock_path.write_text(json.dumps(["not", "mapping"]), encoding="utf-8")
    assert archive_run_records.read_lock_payload(lock_path) == {}
    _ = lock_path.write_text(json.dumps({"run_id": "run"}), encoding="utf-8")
    assert archive_run_records.read_lock_payload(lock_path) == {"run_id": "run"}


def _record(path: Path) -> dict[str, object]:
    decoded = cast(object, json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)
