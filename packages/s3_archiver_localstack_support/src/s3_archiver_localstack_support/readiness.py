"""LocalStack readiness checks shared by tests and manual tooling."""

from __future__ import annotations

import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from botocore.exceptions import BotoCoreError, ClientError
from s3_archiver_core.settings import AppSettings

from s3_archiver_localstack_support.buckets import localstack_admin_client
from s3_archiver_localstack_support.harness import (
    LOCALSTACK_HOST_ENDPOINT,
    localstack_test_env,
    new_localstack_bucket_pair,
)


def wait_for_localstack_readiness(
    *,
    endpoint: str = LOCALSTACK_HOST_ENDPOINT,
    log_dir: str = ".local/localstack-readiness",
    timeout_seconds: float = 90.0,
) -> None:
    """Wait until LocalStack accepts health checks and S3 API calls."""

    parsed = urlparse(endpoint)
    host = parsed.hostname
    port = parsed.port
    if host is None or port is None:
        raise RuntimeError(f"Invalid LOCALSTACK_S3_URL {endpoint!r}")
    health_url = f"{endpoint.rstrip('/')}/_localstack/health"
    deadline = time.monotonic() + timeout_seconds
    settings = AppSettings.from_env(
        localstack_test_env(
            new_localstack_bucket_pair(),
            endpoint=endpoint,
            log_dir=log_dir,
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


def _s3_api_is_ready(settings: AppSettings) -> bool:
    try:
        _ = localstack_admin_client(settings).list_buckets()
    except (BotoCoreError, ClientError):
        return False
    return True


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
