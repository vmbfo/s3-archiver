"""End-to-end runtime probes for scheduler and archive lock behavior."""

from __future__ import annotations

import json
import subprocess
import textwrap
import time
from pathlib import Path
from typing import cast

import pytest

_COMPOSE_RETRY_DELAY_SECONDS = 2.0
_COMPOSE_RUN_RETRIES = 4
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.e2e()
def test_compose_scheduler_waits_for_next_tick_after_lock_refusal(
    compose_env: dict[str, str],
) -> None:
    probe = textwrap.dedent(
        """
        /opt/venv/bin/python - <<'PY'
        import json

        from s3_archiver_cli import main as cli
        from s3_archiver_core.errors import ArchiveRunError

        events = []
        state = {"sleep_calls": 0, "run_attempts": 0}

        def fake_sleep(hour: int, minute: int) -> None:
            state["sleep_calls"] += 1
            events.append(f"sleep-{state['sleep_calls']}:{hour:02d}:{minute:02d}")
            if state["sleep_calls"] == 3:
                print(json.dumps({"events": events}, sort_keys=True))
                raise SystemExit(0)

        def fake_run_archive(settings, log_file):
            _ = (settings, log_file)
            state["run_attempts"] += 1
            events.append(f"run-{state['run_attempts']}")
            if state["run_attempts"] == 1:
                raise ArchiveRunError("archive run lock is already held")
            return {"status": "ok", "run_id": "scheduled-run"}

        cli._sleep_until_next_daily_tick = fake_sleep
        cli._run_archive = fake_run_archive
        cli.schedule(daily_at_utc="04:05")
        PY
        """
    ).strip()
    result = _run_compose(
        compose_env,
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "APP_ENV_FILE=/dev/null",
        "--entrypoint",
        "sh",
        "app",
        "-lc",
        probe,
    )
    payload = _payload(result.stdout)
    error_payload = _payload(result.stderr)

    assert payload["events"] == [
        "sleep-1:04:05",
        "run-1",
        "sleep-2:04:05",
        "run-2",
        "sleep-3:04:05",
    ]
    assert error_payload["message"] == "archive run lock is already held"
    assert error_payload["phase"] == "archive.run"
    assert '"run_id": "scheduled-run"' in result.stdout


@pytest.mark.e2e()
def test_compose_archive_recovers_prior_container_lock_before_archive_work(
    compose_env: dict[str, str],
) -> None:
    writer_probe = textwrap.dedent(
        """
        /opt/venv/bin/python - <<'PY'
        import json
        import os
        import socket
        from datetime import UTC, datetime
        from pathlib import Path

        lock_path = Path("/var/log/s3-archiver/archive.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "run_id": "stale-run",
            "run_started_at_utc": datetime(2026, 4, 20, tzinfo=UTC).isoformat(),
        }
        lock_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        print(json.dumps(payload, sort_keys=True))
        PY
        """
    ).strip()
    recovery_probe = textwrap.dedent(
        """
        /opt/venv/bin/python - <<'PY'
        import json
        import socket
        from datetime import UTC, datetime, timedelta
        from pathlib import Path

        from s3_archiver_cli import main as cli
        from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult
        from s3_archiver_core.archive_manifest import ArchiveManifest
        from s3_archiver_core.settings import AppSettings

        events = []
        recoveries = []

        def fake_recovery(reason, payload):
            events.append(f"recovery:{reason}")
            recoveries.append(payload)

        def fake_health(settings, log_file):
            _ = (settings, log_file)
            events.append("health")
            return object()

        def fake_build(location):
            events.append(f"build:{location.bucket}")
            return object()

        def fake_run_archive(source, destination, options, **kwargs):
            _ = (source, destination, options, kwargs)
            events.append("run_archive")
            started = datetime(2026, 4, 23, tzinfo=UTC)
            return ArchiveRunResult(
                run_id="inner-run-id",
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

        cli._log_lock_recovery = fake_recovery
        cli.run_health_check = fake_health
        cli.build_s3_client = fake_build
        cli.run_archive = fake_run_archive

        settings = AppSettings.from_env(cli._load_runtime_env())
        payload = cli._run_archive(settings, Path("/tmp/s3-archiver.log"))
        print(
            json.dumps(
                {
                    "current_hostname": socket.gethostname(),
                    "events": events,
                    "lock_exists_after": (settings.log_dir / "archive.lock").exists(),
                    "stale_hostname": recoveries[0]["hostname"],
                    "status": payload["status"],
                },
                sort_keys=True,
            )
        )
        PY
        """
    ).strip()
    _ = _run_compose(compose_env, "down", "-v", "--remove-orphans", check=False)
    try:
        writer_result = _run_compose(
            compose_env,
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "APP_ENV_FILE=/dev/null",
            "--entrypoint",
            "sh",
            "app",
            "-lc",
            writer_probe,
        )
        writer_payload = _payload(writer_result.stdout)
        recovery_result = _run_compose(
            compose_env,
            "run",
            "--rm",
            "--no-deps",
            "-e",
            "APP_ENV_FILE=/dev/null",
            "--entrypoint",
            "sh",
            "app",
            "-lc",
            recovery_probe,
        )
        recovery_payload = _payload(recovery_result.stdout)
    finally:
        _ = _run_compose(compose_env, "down", "-v", "--remove-orphans", check=False)

    assert writer_payload["hostname"] != recovery_payload["current_hostname"]
    assert recovery_payload["stale_hostname"] == writer_payload["hostname"]
    assert recovery_payload["status"] == "ok"
    assert recovery_payload["lock_exists_after"] is False
    assert recovery_payload["events"] == [
        "recovery:stale_lock_prior_host",
        "health",
        f"build:{compose_env['TEST_S3_SOURCE_BUCKET']}",
        f"build:{compose_env['TEST_S3_DESTINATION_BUCKET']}",
        "run_archive",
    ]


def _run_compose(
    env: dict[str, str], *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--profile", "test"]
    if args[:1] == ("run",):
        command.append("run")
        command.append("--build")
        command.extend(args[1:])
    else:
        command.extend(args)
    for attempt in range(_COMPOSE_RUN_RETRIES + 1):
        result = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result
        if not check:
            return result
        if attempt == _COMPOSE_RUN_RETRIES or _is_non_retryable_compose_error(result):
            error = subprocess.CalledProcessError(
                result.returncode,
                command,
                output=result.stdout,
                stderr=result.stderr,
            )
            message = "\n".join(
                (
                    f"compose command failed with exit code {result.returncode}: {command}",
                    f"stdout:\n{result.stdout}",
                    f"stderr:\n{result.stderr}",
                )
            )
            raise AssertionError(message) from error
        time.sleep(_COMPOSE_RETRY_DELAY_SECONDS)
    raise AssertionError("compose retry loop exhausted without returning")


def _is_non_retryable_compose_error(result: subprocess.CompletedProcess[str]) -> bool:
    retryable_messages = (
        "No such container",
        "marked for removal",
        'Could not connect to the endpoint URL: "http://localstack:4566/',
    )
    return not any(
        message in result.stderr or message in result.stdout for message in retryable_messages
    )


def _payload(output: str) -> dict[str, object]:
    json_line = next(line for line in reversed(output.splitlines()) if line.startswith("{"))
    return cast(dict[str, object], json.loads(json_line))
