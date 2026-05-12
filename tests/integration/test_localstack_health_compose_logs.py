"""Integration tests covering compose runtime health logs."""

from __future__ import annotations

import subprocess

import pytest
from s3_archiver_localstack_support.compose import find_repo_root, run_app_compose
from s3_archiver_localstack_support.harness import LocalstackBucketPair

REPO_ROOT = find_repo_root()
APP_LOGS_VOLUME = f"{REPO_ROOT.name}_app_logs"


@pytest.mark.integration()
def test_compose_runtime_log_volume_captures_health_logs(
    compose_env: dict[str, str],
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    _ = localstack_bucket_pair
    _reset_app_logs_volume()

    result = _run_compose(compose_env, "run", "--rm", "app", "check")
    volume_log = _read_app_logs_volume()

    assert '"event": "health.succeeded"' in result.stdout
    assert '"event": "health.succeeded"' in volume_log


def _run_compose(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return run_app_compose(env, *args, repo_root=REPO_ROOT)


def _run_volume_probe(command: str) -> subprocess.CompletedProcess[str]:
    probe = ["docker", "run", "--rm", "-v", f"{APP_LOGS_VOLUME}:/logs"]
    probe += ["alpine:3.22", "sh", "-lc", command]
    result = subprocess.run(
        probe,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            probe,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def _read_app_logs_volume() -> str:
    return _run_volume_probe("test -s /logs/s3-archiver.log && cat /logs/s3-archiver.log").stdout


def _reset_app_logs_volume() -> None:
    _ = _run_volume_probe("rm -f /logs/s3-archiver.log /logs/s3-archiver.log.*")
