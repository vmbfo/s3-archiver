"""Integration tests against LocalStack S3."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Literal, cast

import pytest
from botocore.response import StreamingBody
from mypy_boto3_s3.type_defs import VersioningConfigurationTypeDef
from s3_archiver_core.health import run_health_check
from s3_archiver_core.logging_config import configure_logging
from s3_archiver_core.s3 import VersioningState, build_s3_client
from s3_archiver_core.settings import AppSettings

from tests.integration.localstack_harness import (
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    assert_localstack_test_target,
    localstack_test_env,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_LOGS_VOLUME = f"{REPO_ROOT.name}_app_logs"
INTEGRATION_RUNTIME_LOG_DIR = (
    REPO_ROOT / ".local" / "integration-runtime" / "var" / "log" / "s3-archiver"
)


def _integration_env(bucket_pair: LocalstackBucketPair) -> dict[str, str]:
    endpoint = os.environ.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT)
    return localstack_test_env(
        bucket_pair, endpoint=endpoint, log_dir=str(INTEGRATION_RUNTIME_LOG_DIR)
    )


@pytest.mark.integration()
def test_health_check_succeeds_against_isolated_localstack_buckets(
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    _reset_integration_runtime_log_dir()
    settings = AppSettings.from_env(_integration_env(localstack_bucket_pair))
    log_file = configure_logging(settings)
    logger = logging.getLogger("s3_archiver.integration")
    try:
        report = run_health_check(settings, log_file)

        assert report.status == "ok"
        assert settings.log_dir == INTEGRATION_RUNTIME_LOG_DIR
        assert log_file == INTEGRATION_RUNTIME_LOG_DIR / "s3-archiver.log"
        assert log_file.exists()
        records = _log_records(log_file)
        assert any(
            record.get("event") == "health.started"
            and record.get("bucket") == settings.bucket
            and record.get("endpoint_url") == settings.resolved_endpoint_url()
            for record in records
        )
        assert any(
            record.get("event") == "health.succeeded" and record.get("bucket") == settings.bucket
            for record in records
        )
        file_handler = next(
            handler
            for handler in logging.getLogger("s3_archiver").handlers
            if isinstance(handler, TimedRotatingFileHandler)
        )
        file_handler.doRollover()
        _ = logger.info("after rollover", extra={"event": "integration.after-rollover"})

        rotated_files = sorted(settings.log_dir.glob("s3-archiver.log.*"))

        assert len(rotated_files) == 1
        assert (
            re.fullmatch(r"s3-archiver\.log\.\d{4}-\d{2}-\d{2}", rotated_files[0].name) is not None
        )
        assert '"event": "health.succeeded"' in rotated_files[0].read_text(encoding="utf-8")
        assert '"event": "integration.after-rollover"' in log_file.read_text(encoding="utf-8")
    finally:
        _close_logging_handlers()


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


@pytest.mark.integration()
def test_localstack_ready_hook_creates_isolated_buckets(
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    settings = AppSettings.from_env(_integration_env(localstack_bucket_pair))
    client = build_s3_client(settings)

    assert client.head_bucket(Bucket=localstack_bucket_pair.source) is not None
    assert client.head_bucket(Bucket=localstack_bucket_pair.destination) is not None


@pytest.mark.integration()
def test_s3_client_supports_bucket_listing(
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    settings = AppSettings.from_env(_integration_env(localstack_bucket_pair))
    client = build_s3_client(settings)
    key = "integration/listing-probe.txt"
    body = b"listed"

    _ = client.put_object(Bucket=settings.bucket, Key=key, Body=body)
    response = client.list_objects_v2(Bucket=settings.bucket, Prefix="integration/")

    assert key in _listed_keys(response)


@pytest.mark.integration()
def test_s3_client_supports_isolated_object_round_trip(
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    settings = AppSettings.from_env(_integration_env(localstack_bucket_pair))
    client = build_s3_client(settings)
    key = "integration/probe.txt"
    body = b"s3-archiver"

    _ = client.put_object(Bucket=settings.bucket, Key=key, Body=body)
    response = client.get_object(Bucket=settings.bucket, Key=key)
    payload = cast(StreamingBody, response["Body"]).read()

    assert payload == body


@pytest.mark.integration()
def test_localstack_guard_rejects_live_s3_endpoint(
    localstack_bucket_pair: LocalstackBucketPair,
) -> None:
    unsafe_env = _integration_env(localstack_bucket_pair)
    unsafe_env["S3_DESTINATION_ENDPOINT_URL"] = "https://s3.amazonaws.com"

    with pytest.raises(RuntimeError, match="not allowed"):
        assert_localstack_test_target(unsafe_env)


@pytest.mark.integration()
@pytest.mark.parametrize(
    ("status", "expected_state"),
    [(None, "Disabled"), ("Enabled", "Enabled"), ("Suspended", "Suspended")],
)
def test_health_check_succeeds_for_source_bucket_versioning_states(
    localstack_bucket_pair: LocalstackBucketPair,
    status: Literal["Enabled", "Suspended"] | None,
    expected_state: VersioningState,
) -> None:
    settings = AppSettings.from_env(_integration_env(localstack_bucket_pair))
    client = build_s3_client(settings)
    if status is not None:
        configuration: VersioningConfigurationTypeDef = {"Status": status}
        _ = client.put_bucket_versioning(
            Bucket=settings.bucket,
            VersioningConfiguration=configuration,
        )

    raw_state = client.get_bucket_versioning(Bucket=settings.bucket).get("Status")
    state: VersioningState = (
        cast(VersioningState, raw_state) if raw_state in {"Enabled", "Suspended"} else "Disabled"
    )
    report = run_health_check(settings, settings.log_dir / "s3-archiver.log")

    assert state == expected_state
    assert report.status == "ok"
    assert (report.source_bucket, report.source_versioning) == (settings.bucket, expected_state)


def _listed_keys(response: Mapping[str, object]) -> set[str]:
    contents = response.get("Contents")
    if not isinstance(contents, list):
        return set()
    keys: set[str] = set()
    for entry_obj in cast(list[object], contents):
        if not isinstance(entry_obj, dict):
            continue
        entry = cast(dict[str, object], entry_obj)
        key = entry.get("Key")
        if isinstance(key, str):
            keys.add(key)
    return keys


def _log_records(log_file: Path) -> list[dict[str, object]]:
    return [
        cast(dict[str, object], json.loads(line))
        for line in log_file.read_text(encoding="utf-8").splitlines()
    ]


def _close_logging_handlers() -> None:
    for handler in logging.getLogger("s3_archiver").handlers:
        handler.close()


def _run_compose(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--profile", "test", *args]
    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            command,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


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


def _reset_integration_runtime_log_dir() -> None:
    shutil.rmtree(INTEGRATION_RUNTIME_LOG_DIR, ignore_errors=True)
