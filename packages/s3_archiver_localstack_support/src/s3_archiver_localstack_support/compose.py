"""Shared Docker Compose helpers for tests and manual tooling."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Collection, Sequence
from pathlib import Path

from s3_archiver_localstack_support._common import is_retryable_localstack_message

_COMPOSE_RETRY_DELAY_SECONDS = 2.0
_COMPOSE_RUN_RETRIES = 4
_DEFAULT_RETRYABLE_MESSAGES = (
    "No such container",
    "marked for removal",
    'Could not connect to the endpoint URL: "http://localstack:4566/',
    'Could not connect to the endpoint URL: "http://localhost:4566/',
)
APP_RETRYABLE_MESSAGES = (
    "HeadBucket operation: Not Found",
    "Connection was closed before we received a valid response",
    'optional dependency "localstack" failed to start',
    "exited (137)",
    "unable to upgrade to tcp, received 404",
    "unable to upgrade to tcp, received 409",
    "app is missing dependency localstack",
    "network s3-archiver_default not found",
    'container name "/s3-archiver-localstack-1" is already in use',
)
APP_RETRYABLE_RETURNCODES = (4, 137)
STACK_RETRYABLE_MESSAGES = ("HeadBucket operation: Not Found",)
STACK_RETRYABLE_RETURNCODES = (137,)


def run_compose(
    env: dict[str, str],
    *args: str,
    check: bool = True,
    retryable_messages: Collection[str] = (),
    retryable_returncodes: Collection[int] = (),
    retries: int = _COMPOSE_RUN_RETRIES,
    retry_delay_seconds: float = _COMPOSE_RETRY_DELAY_SECONDS,
    repo_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a Docker Compose command with retry handling for LocalStack startup races."""

    command = compose_command(*args)
    for attempt in range(retries + 1):
        result = subprocess.run(
            command,
            cwd=find_repo_root() if repo_root is None else repo_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result
        if not check:
            return result
        if attempt == retries or not is_retryable_compose_result(
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
        time.sleep(retry_delay_seconds)
    raise AssertionError("compose retry loop exhausted without returning")  # pragma: no cover


def run_app_compose(
    env: dict[str, str],
    *args: str,
    check: bool = True,
    repo_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Compose with retry handling for app service LocalStack races."""

    return run_compose(
        env,
        *args,
        check=check,
        retryable_messages=APP_RETRYABLE_MESSAGES,
        retryable_returncodes=APP_RETRYABLE_RETURNCODES,
        repo_root=repo_root,
    )


def run_stack_compose(
    env: dict[str, str],
    *args: str,
    check: bool = True,
    extra_retryable_returncodes: Collection[int] = (),
    repo_root: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run Compose with retry handling for stack-level LocalStack races."""

    return run_compose(
        env,
        *args,
        check=check,
        retryable_messages=STACK_RETRYABLE_MESSAGES,
        retryable_returncodes=(*STACK_RETRYABLE_RETURNCODES, *extra_retryable_returncodes),
        repo_root=repo_root,
    )


def compose_command(
    *args: str,
    run_options: Sequence[str] = (),
    build_run: bool = True,
) -> list[str]:
    """Return the Docker Compose command used by LocalStack validation tooling."""

    command = ["docker", "compose", "--profile", "test"]
    if args[:1] == ("run",):
        build_options = ("--build",) if build_run else ()
        return [*command, "run", *build_options, *run_options, *args[1:]]
    return [*command, *args]


def is_retryable_compose_result(
    result: subprocess.CompletedProcess[str],
    *,
    retryable_messages: Collection[str],
    retryable_returncodes: Collection[int],
) -> bool:
    """Return whether a failed Compose result matches known retryable startup races."""

    if result.returncode in retryable_returncodes:
        return True
    messages = (*_DEFAULT_RETRYABLE_MESSAGES, *retryable_messages)
    output = f"{result.stderr}\n{result.stdout}"
    return is_retryable_localstack_message(output, extra_fragments=messages)


def find_repo_root() -> Path:
    """Return the repository root containing compose.yaml."""

    for parent in Path(__file__).resolve().parents:
        if (parent / "compose.yaml").exists():
            return parent
    raise RuntimeError("Could not find repository root containing compose.yaml")  # pragma: no cover
