"""Shared test helpers."""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Generator, Mapping
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

import pytest
from botocore.exceptions import BotoCoreError, ClientError
from s3_archiver_core.s3 import build_s3_client
from s3_archiver_core.settings import AppSettings

from tests.integration.localstack_harness import (
    LOCALSTACK_COMPOSE_ENDPOINT,
    LOCALSTACK_HOST_ENDPOINT,
    LocalstackBucketPair,
    LocalstackS3AdminClient,
    assert_localstack_test_target,
    bucket_pair_from_env,
    compose_runtime_log_dir,
    delete_localstack_bucket,
    ensure_localstack_bucket,
    localstack_test_env,
    new_localstack_bucket_pair,
    write_localstack_env_file,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_COMPOSE_RETRY_DELAY_SECONDS = 1.0
_COMPOSE_UP_RETRIES = 3


@pytest.fixture()
def base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "S3_SOURCE_PROVIDER": "oci",
        "S3_SOURCE_ACCESS_KEY_ID": "access-key",
        "S3_SOURCE_SECRET_ACCESS_KEY": "secret-key",
        "S3_SOURCE_REGION": "eu-frankfurt-1",
        "S3_SOURCE_NAMESPACE": "tenant",
        "S3_SOURCE_BUCKET": "archive-bucket",
        "S3_SOURCE_IAM_USER_OCID": "ocid1.user.oc1..example",
        "S3_SOURCE_ENDPOINT_URL": (
            "https://tenant.compat.objectstorage.eu-frankfurt-1.oraclecloud.com"
        ),
        "S3_SOURCE_ADDRESSING_STYLE": "path",
        "S3_DESTINATION_PROVIDER": "localstack",
        "S3_DESTINATION_ACCESS_KEY_ID": "destination-access",
        "S3_DESTINATION_SECRET_ACCESS_KEY": "destination-secret",
        "S3_DESTINATION_REGION": "us-east-1",
        "S3_DESTINATION_BUCKET": "destination-bucket",
        "S3_DESTINATION_ENDPOINT_URL": "http://localstack:4566",
        "S3_DESTINATION_ADDRESSING_STYLE": "path",
        "ARCHIVER_CONFIG_JSON": (
            '[{"name":"default","parser":"filename_timestamp","copy_mode":"daily_tar_gz",'
            '"source":{"provider":"${S3_SOURCE_PROVIDER}",'
            '"endpoint_url":"${S3_SOURCE_ENDPOINT_URL}",'
            '"region":"${S3_SOURCE_REGION}","namespace":"${S3_SOURCE_NAMESPACE}",'
            '"bucket":"${S3_SOURCE_BUCKET}","iam_user_ocid":"${S3_SOURCE_IAM_USER_OCID}",'
            '"path":"","access_key_id":"${S3_SOURCE_ACCESS_KEY_ID}",'
            '"secret_access_key":"${S3_SOURCE_SECRET_ACCESS_KEY}",'
            '"addressing_style":"${S3_SOURCE_ADDRESSING_STYLE}"},'
            '"destination":{"provider":"${S3_DESTINATION_PROVIDER}",'
            '"endpoint_url":"${S3_DESTINATION_ENDPOINT_URL}",'
            '"region":"${S3_DESTINATION_REGION}","bucket":"${S3_DESTINATION_BUCKET}",'
            '"path":"",'
            '"access_key_id":"${S3_DESTINATION_ACCESS_KEY_ID}",'
            '"secret_access_key":"${S3_DESTINATION_SECRET_ACCESS_KEY}",'
            '"addressing_style":"${S3_DESTINATION_ADDRESSING_STYLE}"}}]'
        ),
        "LOG_LEVEL": "INFO",
        "LOG_DIR": str(tmp_path / "logs"),
    }


@pytest.fixture()
def compose_env(tmp_path: Path) -> dict[str, str]:
    bucket_pair = new_localstack_bucket_pair()
    env = os.environ.copy()
    localstack_host_endpoint = os.environ.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT)
    localstack_host_port = urlparse(localstack_host_endpoint).port
    env["APP_ENV_FILE"] = str(
        write_localstack_env_file(
            tmp_path,
            bucket_pair,
            endpoint=LOCALSTACK_COMPOSE_ENDPOINT,
            log_dir=compose_runtime_log_dir(bucket_pair),
        )
    )
    env["LOCALSTACK_S3_URL"] = localstack_host_endpoint
    if localstack_host_port is not None:
        env["LOCALSTACK_HOST_PORT"] = str(localstack_host_port)
    env["TEST_S3_SOURCE_BUCKET"] = bucket_pair.source
    env["TEST_S3_DESTINATION_BUCKET"] = bucket_pair.destination
    return env


@pytest.fixture()
def localstack_service(compose_env: dict[str, str]) -> Generator[None, None, None]:
    _ = _run_compose(compose_env, "down", "-v", "--remove-orphans", check=False)
    try:
        _ = _run_compose(
            compose_env,
            "up",
            "-d",
            "localstack",
            retries=_COMPOSE_UP_RETRIES,
        )
        _wait_for_localstack_readiness(env=compose_env)
        yield
    finally:
        _ = _run_compose(compose_env, "down", "-v", "--remove-orphans", check=False)


@pytest.fixture()
def localstack_bucket_pair(
    compose_env: dict[str, str],
    localstack_service: None,
) -> Generator[LocalstackBucketPair, None, None]:
    _ = localstack_service
    bucket_pair = bucket_pair_from_env(compose_env)
    test_env = localstack_test_env(
        bucket_pair,
        endpoint=compose_env.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT),
        log_dir=str(REPO_ROOT / ".local" / "pytest-logs"),
    )
    assert_localstack_test_target(test_env)
    settings = AppSettings.from_env(test_env)
    client = cast(LocalstackS3AdminClient, _as_object(build_s3_client(settings.routes[0].source)))
    ensure_localstack_bucket(client, bucket_pair.source)
    ensure_localstack_bucket(client, bucket_pair.destination)
    try:
        yield bucket_pair
    finally:
        _delete_bucket_pair(client, bucket_pair)


def _run_compose(
    env: dict[str, str],
    *args: str,
    check: bool = True,
    retries: int = 0,
) -> subprocess.CompletedProcess[str]:
    command = ["docker", "compose", "--profile", "test", *args]
    for attempt in range(retries + 1):
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
        if attempt == retries or _is_non_retryable_compose_error(args, result):
            raise subprocess.CalledProcessError(
                result.returncode,
                command,
                output=result.stdout,
                stderr=result.stderr,
            )
        time.sleep(_COMPOSE_RETRY_DELAY_SECONDS)
    raise AssertionError("compose retry loop exhausted without returning")


def _wait_for_localstack_readiness(
    timeout_seconds: float = 90.0,
    *,
    env: Mapping[str, str] | None = None,
) -> None:
    endpoint_source = os.environ if env is None else env
    endpoint = endpoint_source.get("LOCALSTACK_S3_URL", LOCALSTACK_HOST_ENDPOINT)
    parsed = urlparse(endpoint)
    host = parsed.hostname
    port = parsed.port
    if host is None or port is None:
        raise RuntimeError(f"Invalid LOCALSTACK_S3_URL {endpoint!r}")
    deadline = time.monotonic() + timeout_seconds
    health_url = f"{endpoint.rstrip('/')}/_localstack/health"
    bucket_pair = new_localstack_bucket_pair()
    settings = AppSettings.from_env(
        localstack_test_env(
            bucket_pair,
            endpoint=endpoint,
            log_dir=str(REPO_ROOT / ".local" / "pytest-logs"),
        )
    )
    while time.monotonic() < deadline:
        if (
            _can_connect(host, port)
            and _healthcheck_responds(health_url)
            and _s3_api_is_ready(settings)
        ):
            return
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for LocalStack host endpoint {endpoint!r}")


def _can_connect(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((host, port)) == 0


def _healthcheck_responds(health_url: str) -> bool:
    try:
        with urlopen(health_url, timeout=1.0):
            return True
    except (HTTPError, URLError, OSError):
        return False


def _bucket_is_ready(settings: AppSettings) -> bool:
    route = settings.routes[0]
    client = cast(LocalstackS3AdminClient, _as_object(build_s3_client(route.source)))
    try:
        _ = client.head_bucket(Bucket=route.source.bucket)
    except (BotoCoreError, ClientError):
        return False
    return True


def _s3_api_is_ready(settings: AppSettings) -> bool:
    if _bucket_is_ready is not _ORIGINAL_BUCKET_IS_READY:
        return _bucket_is_ready(settings)
    client = cast(LocalstackS3AdminClient, _as_object(build_s3_client(settings.routes[0].source)))
    try:
        _ = client.list_buckets()
    except (BotoCoreError, ClientError):
        return False
    return True


_ORIGINAL_BUCKET_IS_READY = _bucket_is_ready


def _as_object(value: object) -> object:
    return value


def _delete_bucket_pair(client: LocalstackS3AdminClient, bucket_pair: LocalstackBucketPair) -> None:
    failures: list[str] = []
    for bucket in (bucket_pair.source, bucket_pair.destination):
        try:
            delete_localstack_bucket(client, bucket)
        except RuntimeError as exc:
            failures.append(str(exc))
    if failures:
        raise RuntimeError("Failed to tear down LocalStack buckets: " + "; ".join(failures))


def _is_non_retryable_compose_error(
    args: tuple[str, ...],
    result: subprocess.CompletedProcess[str],
) -> bool:
    if args and args[0] == "up":
        return False
    retryable_messages = ("No such container", "marked for removal")
    return not any(message in result.stderr for message in retryable_messages)
