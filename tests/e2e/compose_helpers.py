"""Shared Docker Compose helpers for end-to-end tests."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Collection

_COMPOSE_RETRY_DELAY_SECONDS = 2.0
_COMPOSE_RUN_RETRIES = 4
_DEFAULT_RETRYABLE_MESSAGES = (
    "No such container",
    "marked for removal",
    'Could not connect to the endpoint URL: "http://localstack:4566/',
)
REPO_ROOT = Path(__file__).resolve().parents[2]


def run_compose(
    env: dict[str, str],
    *args: str,
    check: bool = True,
    retryable_messages: Collection[str] = (),
    retryable_returncodes: Collection[int] = (),
) -> subprocess.CompletedProcess[str]:
    command = _compose_command(*args)
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
        if attempt == _COMPOSE_RUN_RETRIES or _is_non_retryable_compose_error(
            result,
            retryable_messages=retryable_messages,
            retryable_returncodes=retryable_returncodes,
        ):
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


def _compose_command(*args: str) -> list[str]:
    command = ["docker", "compose", "--profile", "test"]
    if args[:1] == ("run",):
        return [*command, "run", "--build", *args[1:]]
    return [*command, *args]


def _is_non_retryable_compose_error(
    result: subprocess.CompletedProcess[str],
    *,
    retryable_messages: Collection[str],
    retryable_returncodes: Collection[int],
) -> bool:
    if result.returncode in retryable_returncodes:
        return False
    messages = (*_DEFAULT_RETRYABLE_MESSAGES, *retryable_messages)
    return not any(message in result.stderr or message in result.stdout for message in messages)
