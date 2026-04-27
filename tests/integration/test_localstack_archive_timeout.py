"""Integration test for archive command timeout isolation."""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from typing import TypedDict, cast

import pytest
import s3_archiver_cli.main as cli_module
import s3_archiver_cli.scheduled_archive as scheduled_archive_module
from s3_archiver_core.settings import AppSettings
from typer.testing import CliRunner

from tests.integration.localstack_harness import (
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    localstack_test_env,
)

RUNNER = CliRunner()


class SchedulerErrorPayload(TypedDict):
    message: str
    phase: str
    field: str
    reason: str
    timed_out: bool


@pytest.mark.integration()
def test_archive_command_times_out_without_late_child_mutation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = localstack_test_env(
        LocalstackBucketPair("s3-archiver-timeout-source", "s3-archiver-timeout-destination"),
        endpoint=os.environ.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT),
        log_dir=str(tmp_path / "logs"),
    )
    env["ARCHIVER_RUN_TIMEOUT"] = "1s"
    settings = AppSettings.from_env(env)
    lock_path = settings.log_dir / "archive.lock"
    marker_path = tmp_path / "late-mutation.txt"
    timeout_probe = textwrap.dedent(
        f"""
        import json
        import time
        from datetime import UTC, datetime, timedelta
        from pathlib import Path

        from s3_archiver_core.archive_lock import FileArchiveRunLock

        lock = FileArchiveRunLock(Path({str(lock_path)!r}))
        if not lock.acquire(
            run_id="timed-out-run",
            run_started_at_utc=datetime.now(tz=UTC),
            timeout=timedelta(seconds=1),
        ):
            raise SystemExit("failed to acquire archive lock")
        print(json.dumps({{"lock_acquired": True}}), flush=True)
        time.sleep(2)
        Path({str(marker_path)!r}).write_text("late mutation\\n", encoding="utf-8")
        print(json.dumps({{"mutated": True}}), flush=True)
        """
    ).strip()

    def fake_archive_child_command() -> list[str]:
        return [sys.executable, "-c", timeout_probe]

    monkeypatch.setattr(os, "environ", env)
    monkeypatch.setattr(
        scheduled_archive_module,
        "archive_child_command",
        fake_archive_child_command,
    )

    result = RUNNER.invoke(cli_module.app, ["archive"])

    assert result.exit_code == 1
    assert '"lock_acquired": true' in result.stdout
    assert '"mutated": true' not in result.stdout
    payload = _last_error_payload(result.stderr)
    assert payload["phase"] == "archive.run"
    assert payload["field"] == "ARCHIVER_RUN_TIMEOUT"
    assert payload["message"] == "archive run timed out"
    assert payload["reason"] == "archive_run_timeout"
    assert payload["timed_out"] is True
    assert not marker_path.exists()
    assert not lock_path.exists()


def _last_json(output: str) -> dict[str, object]:
    json_line = next(line for line in reversed(output.splitlines()) if line.startswith("{"))
    return cast(dict[str, object], json.loads(json_line))


def _last_error_payload(output: str) -> SchedulerErrorPayload:
    return cast(SchedulerErrorPayload, cast(object, _last_json(output)))
