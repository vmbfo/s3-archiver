"""End-to-end tests for the Docker Compose stack."""

from __future__ import annotations

import subprocess
import time

import pytest

_COMPOSE_RETRY_DELAY_SECONDS = 2.0
_COMPOSE_RUN_RETRIES = 4


@pytest.mark.e2e()
def test_compose_app_healthcheck_succeeds(
    compose_env: dict[str, str],
    localstack_service: None,
) -> None:
    _ = localstack_service
    result = _run_compose(compose_env, "run", "--rm", "app")
    final_line = result.stdout.strip().splitlines()[-1]

    assert '"status": "ok"' in final_line
    assert '"bucket": "s3-archiver-integration"' in final_line


@pytest.mark.e2e()
def test_compose_app_writes_persisted_logs(
    compose_env: dict[str, str],
    localstack_service: None,
) -> None:
    _ = localstack_service
    _ = _run_compose(compose_env, "run", "--rm", "app")
    result = _run_compose(
        compose_env,
        "run",
        "--rm",
        "app",
        "sh",
        "-lc",
        "test -s /var/log/s3-archiver/s3-archiver.log && cat /var/log/s3-archiver/s3-archiver.log",
    )

    assert '"event": "health.succeeded"' in result.stdout


def _run_compose(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--profile", "test", *args]
    for attempt in range(_COMPOSE_RUN_RETRIES + 1):
        result = subprocess.run(
            command,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result
        if attempt == _COMPOSE_RUN_RETRIES or _is_non_retryable_compose_error(result):
            raise subprocess.CalledProcessError(
                result.returncode,
                command,
                output=result.stdout,
                stderr=result.stderr,
            )
        time.sleep(_COMPOSE_RETRY_DELAY_SECONDS)
    raise AssertionError("compose retry loop exhausted without returning")


def _is_non_retryable_compose_error(result: subprocess.CompletedProcess[str]) -> bool:
    retryable_messages = (
        "No such container",
        "marked for removal",
        "HeadBucket operation: Not Found",
        'Could not connect to the endpoint URL: "http://localstack:4566/',
    )
    if result.returncode == 137:
        return False
    return not any(
        message in result.stderr or message in result.stdout for message in retryable_messages
    )
