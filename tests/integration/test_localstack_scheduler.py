"""Integration tests for scheduler and archive lock coordination."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
import s3_archiver_cli.main as cli_module
from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult
from s3_archiver_core.archive_manifest import ArchiveManifest
from s3_archiver_core.settings import AppSettings, S3LocationSettings

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
    log_file = settings.log_dir / "s3-archiver.log"
    active_lock = _start_active_lock(settings.log_dir / "archive.lock")
    events: list[str] = []
    sleep_calls = 0

    def configure(_settings: AppSettings) -> Path:
        return log_file

    def fake_sleep_until_tick(hour: int, minute: int) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        events.append(f"sleep-{sleep_calls}:{hour:02d}:{minute:02d}")
        if sleep_calls == 2:
            active_lock.terminate()
            _ = active_lock.wait(timeout=5)
        if sleep_calls >= 3:
            raise RuntimeError("stop scheduler integration test")

    def run_health(_settings: AppSettings, _log_file: Path) -> object:
        events.append("health")
        return object()

    def build_client(location: S3LocationSettings) -> object:
        events.append(f"build:{location.bucket}")
        return object()

    def run_core_archive(
        source: object,
        destination: object,
        options: object,
        **kwargs: object,
    ) -> ArchiveRunResult:
        _ = (source, destination, options, kwargs)
        events.append("run_archive")
        started = datetime(2026, 4, 23, tzinfo=UTC)
        return ArchiveRunResult(
            run_id="scheduled-run",
            manifest=ArchiveManifest(
                run_started_at_utc=started,
                retention_cutoff_utc=started - timedelta(days=60),
                entries=(),
            ),
            copy=ArchivePhaseResult("copy"),
            verify=ArchivePhaseResult("verify"),
            cleanup=ArchivePhaseResult("cleanup"),
            list=ArchivePhaseResult("list"),
        )

    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "_sleep_until_next_daily_tick", fake_sleep_until_tick)
    monkeypatch.setattr(cli_module, "run_health_check", run_health)
    monkeypatch.setattr(cli_module, "build_s3_client", build_client)
    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    with pytest.raises(RuntimeError, match="stop scheduler integration test"):
        cli_module.schedule(daily_at_utc="04:05")

    captured = capsys.readouterr()
    error_payload = _last_json(captured.err)
    success_payload = _last_json(captured.out)
    assert error_payload["message"] == "archive run lock is already held"
    assert error_payload["phase"] == "archive.run"
    assert success_payload["status"] == "ok"
    assert events == [
        "sleep-1:04:05",
        "sleep-2:04:05",
        "health",
        f"build:{localstack_bucket_pair.source}",
        f"build:{localstack_bucket_pair.destination}",
        "run_archive",
        "sleep-3:04:05",
    ]


@pytest.mark.integration()
def test_run_archive_recovers_prior_host_lock_before_archive_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    env = _integration_env(tmp_path, localstack_bucket_pair)
    settings = AppSettings.from_env(env)
    lock_path = settings.log_dir / "archive.lock"
    stale_payload = {
        "hostname": "prior-container-host",
        "pid": 4321,
        "run_id": "stale-run",
        "run_started_at_utc": datetime(2026, 4, 20, tzinfo=UTC).isoformat(),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _ = lock_path.write_text(json.dumps(stale_payload), encoding="utf-8")
    events: list[str] = []

    def log_recovery(reason: str, payload: dict[str, object]) -> None:
        events.append(f"recovery:{reason}")
        assert payload == stale_payload

    def run_health(_settings: AppSettings, _log_file: Path) -> object:
        events.append("health")
        return object()

    def build_client(location: S3LocationSettings) -> object:
        events.append(f"build:{location.bucket}")
        return object()

    def run_core_archive(
        source: object,
        destination: object,
        options: object,
        **kwargs: object,
    ) -> ArchiveRunResult:
        _ = (source, destination, options, kwargs)
        events.append("run_archive")
        started = datetime(2026, 4, 23, tzinfo=UTC)
        return ArchiveRunResult(
            run_id="archive-run",
            manifest=ArchiveManifest(
                run_started_at_utc=started,
                retention_cutoff_utc=started - timedelta(days=60),
                entries=(),
            ),
            copy=ArchivePhaseResult("copy"),
            verify=ArchivePhaseResult("verify"),
            cleanup=ArchivePhaseResult("cleanup"),
            list=ArchivePhaseResult("list"),
        )

    monkeypatch.setattr(cli_module, "_log_lock_recovery", log_recovery)
    monkeypatch.setattr(cli_module, "run_health_check", run_health)
    monkeypatch.setattr(cli_module, "build_s3_client", build_client)
    monkeypatch.setattr(cli_module, "run_archive", run_core_archive)

    payload = cli_module._run_archive(settings, settings.log_dir / "s3-archiver.log")

    assert payload["status"] == "ok"
    assert not lock_path.exists()
    assert events == [
        "recovery:stale_lock_prior_host",
        "health",
        f"build:{localstack_bucket_pair.source}",
        f"build:{localstack_bucket_pair.destination}",
        "run_archive",
    ]


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
