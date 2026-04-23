"""Integration tests for scheduler and archive lock coordination."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import s3_archiver_cli.main as cli_module
from s3_archiver_core.logging_config import configure_logging
from s3_archiver_core.settings import AppSettings

from tests.integration.localstack_harness import (
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    localstack_test_env,
)


@pytest.mark.integration()
def test_schedule_skips_immediate_replay_after_lock_refusal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    env = _integration_env(tmp_path, localstack_bucket_pair)
    settings = AppSettings.from_env(env)
    active_lock = _start_active_lock(settings.log_dir / "archive.lock")
    sleep_calls = 0

    def fake_sleep_until_tick(hour: int, minute: int) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 2:
            active_lock.terminate()
            _ = active_lock.wait(timeout=5)
        if sleep_calls >= 3:
            raise RuntimeError("stop scheduler integration test")

    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(cli_module, "_sleep_until_next_daily_tick", fake_sleep_until_tick)

    try:
        with pytest.raises(RuntimeError, match="stop scheduler integration test"):
            cli_module.schedule(daily_at_utc="04:05")
    finally:
        if active_lock.poll() is None:
            active_lock.terminate()
            _ = active_lock.wait(timeout=5)

    captured = capsys.readouterr()
    error_payload = _last_json(captured.err)
    success_payload = _last_json(captured.out)
    assert error_payload["message"] == "archive run lock is already held"
    assert error_payload["phase"] == "archive.run"
    assert success_payload["status"] == "ok"
    log_file = settings.log_dir / "s3-archiver.log"
    assert log_file.exists()
    log_text = log_file.read_text(encoding="utf-8")
    assert '"event": "archive.lock.recovered"' in log_text
    assert '"reason": "stale_lock_abandoned"' in log_text


@pytest.mark.integration()
def test_run_archive_recovers_prior_host_lock_before_archive_work(
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    env = _integration_env(tmp_path, localstack_bucket_pair)
    settings = AppSettings.from_env(env)
    lock_path = settings.log_dir / "archive.lock"
    log_file = configure_logging(settings)
    stale_payload = {
        "hostname": "prior-container-host",
        "pid": 4321,
        "run_id": "stale-run",
        "run_started_at_utc": datetime(2026, 4, 20, tzinfo=UTC).isoformat(),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _ = lock_path.write_text(json.dumps(stale_payload), encoding="utf-8")

    payload = cli_module._run_archive(settings, log_file)

    assert payload["status"] == "ok"
    assert not lock_path.exists()
    log_text = log_file.read_text(encoding="utf-8")
    assert '"event": "archive.lock.recovered"' in log_text
    assert '"reason": "stale_lock_prior_host"' in log_text


def _integration_env(tmp_path: Path, bucket_pair: LocalstackBucketPair) -> dict[str, str]:
    return localstack_test_env(
        bucket_pair,
        endpoint=os.environ.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT),
        log_dir=str(tmp_path / "logs"),
    )


def _start_active_lock(lock_path: Path) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(["sleep", "30"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "hostname": socket.gethostname(),
        "pid": process.pid,
        "run_id": "active-run",
        "run_started_at_utc": datetime.now(tz=UTC).isoformat(),
    }
    _ = lock_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return process


def _last_json(output: str) -> dict[str, object]:
    json_line = next(line for line in reversed(output.splitlines()) if line.startswith("{"))
    return cast(dict[str, object], json.loads(json_line))
