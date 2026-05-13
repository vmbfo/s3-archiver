"""Unit tests for LocalStack support runtime helpers."""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, TracebackType
from typing import cast, override
from urllib.error import URLError

import pytest
import s3_archiver_localstack_support.compose as compose_module
import s3_archiver_localstack_support.harness as harness_module
import s3_archiver_localstack_support.readiness as readiness_module
from botocore.exceptions import ClientError
from s3_archiver_core.settings import AppSettings

from tests.unit.localstack_support_fakes import FakeAdminClient

pytestmark = pytest.mark.unit()


def test_compose_wrappers_and_find_repo_root(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    run_calls: list[tuple[str, ...]] = []

    def fake_subprocess_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        run_calls.append(tuple(command))
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="ok", stderr="")

    compose_subprocess = cast(ModuleType, compose_module.__dict__["subprocess"])
    monkeypatch.setattr(compose_subprocess, "run", fake_subprocess_run)
    result = compose_module.run_compose({}, "version", check=False)
    assert result.returncode in {0, 1}
    assert run_calls == [("docker", "compose", "--profile", "test", "version")]
    assert compose_module.run_compose({}, "run", "--rm", "app", check=False).stdout == "ok"
    assert (compose_module.find_repo_root() / "compose.yaml").exists()

    def failed_subprocess_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=command, returncode=2, stdout="out", stderr="err")

    monkeypatch.setattr(compose_subprocess, "run", failed_subprocess_run)
    assert compose_module.run_compose({}, "ps", check=False).returncode == 2
    with pytest.raises(AssertionError, match="compose command failed with exit code 2"):
        _ = compose_module.run_compose({}, "ps")

    retry_results = iter(
        (
            subprocess.CompletedProcess(
                args=("docker",),
                returncode=137,
                stdout="",
                stderr="",
            ),
            subprocess.CompletedProcess(args=("docker",), returncode=0, stdout="ok", stderr=""),
        )
    )

    def retry_subprocess_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        _ = command
        return next(retry_results)

    compose_time = cast(ModuleType, compose_module.__dict__["time"])

    def ignore_compose_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(compose_subprocess, "run", retry_subprocess_run)
    monkeypatch.setattr(compose_time, "sleep", ignore_compose_sleep)
    assert compose_module.run_compose({}, "up", retryable_returncodes=(137,)).stdout == "ok"

    retry_results = iter(
        (
            subprocess.CompletedProcess(
                args=("docker",),
                returncode=1,
                stdout="",
                stderr="No such container",
            ),
            subprocess.CompletedProcess(args=("docker",), returncode=0, stdout="ok", stderr=""),
        )
    )

    monkeypatch.setattr(compose_subprocess, "run", retry_subprocess_run)
    assert compose_module.run_compose({}, "up").stdout == "ok"

    retry_results = iter(
        (
            subprocess.CompletedProcess(
                args=("docker",),
                returncode=255,
                stdout="",
                stderr='Could not connect to the endpoint URL: "http://localhost:4566/"',
            ),
            subprocess.CompletedProcess(args=("docker",), returncode=0, stdout="ok", stderr=""),
        )
    )

    monkeypatch.setattr(compose_subprocess, "run", retry_subprocess_run)
    assert compose_module.run_compose({}, "up").stdout == "ok"

    def fake_run_compose(
        env: dict[str, str], *args: str, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"env": env, "args": args, "kwargs": kwargs})
        return subprocess.CompletedProcess(args=("docker",), returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(compose_module, "run_compose", fake_run_compose)

    assert compose_module.run_app_compose({}, "run").stdout == "ok"
    assert (
        compose_module.run_stack_compose(
            {},
            "up",
            extra_retryable_returncodes=(42,),
            repo_root=Path.cwd(),
        ).stdout
        == "ok"
    )
    first_kwargs = cast(dict[str, object], calls[0]["kwargs"])
    second_kwargs = cast(dict[str, object], calls[1]["kwargs"])
    assert first_kwargs["retryable_returncodes"] == compose_module.APP_RETRYABLE_RETURNCODES
    assert second_kwargs["retryable_returncodes"] == (137, 42)


def test_compose_command_run_build_option_is_explicit() -> None:
    base_command = ["docker", "compose", "--profile", "test", "run"]
    assert compose_module.compose_command("run", "--rm", "app") == [*base_command, "--rm", "app"]
    assert compose_module.compose_command("run", "--rm", "app", build_run=True) == [
        *base_command,
        "--build",
        "--rm",
        "app",
    ]


def test_readiness_timeout() -> None:
    with pytest.raises(RuntimeError, match="Timed out"):
        readiness_module.wait_for_localstack_readiness(
            endpoint=harness_module.LOCALSTACK_HOST_ENDPOINT,
            timeout_seconds=0,
        )


def test_localstack_compose_env_writes_overrides_and_ports(tmp_path: Path) -> None:
    pair = harness_module.LocalstackBucketPair("source", "destination")
    override_env = harness_module.write_localstack_env_file(
        tmp_path,
        pair,
        endpoint=harness_module.LOCALSTACK_HOST_ENDPOINT,
        log_dir="/tmp/logs",
        filename="override.env",
        overrides={"LOG_LEVEL": "WARNING"},
    )
    compose_env = harness_module.localstack_compose_env(
        pair,
        app_env_file=override_env,
        environ={"LOCALSTACK_S3_URL": "http://127.0.0.1:4666"},
    )
    no_port_env = harness_module.localstack_compose_env(
        pair,
        app_env_file=override_env,
        environ={"LOCALSTACK_S3_URL": "http://localstack"},
    )

    assert "LOG_LEVEL=WARNING" in override_env.read_text(encoding="utf-8")
    assert compose_env["APP_ENV_FILE"] == str(override_env)
    assert compose_env["LOCALSTACK_HOST_PORT"] == "4666"
    assert compose_env["TEST_S3_SOURCE_BUCKET"] == "source"
    assert "LOCALSTACK_HOST_PORT" not in no_port_env


def test_readiness_rejects_invalid_endpoint() -> None:
    with pytest.raises(RuntimeError, match="Invalid LOCALSTACK_S3_URL"):
        readiness_module.wait_for_localstack_readiness(endpoint="not-a-url", timeout_seconds=0)


def test_readiness_waits_until_socket_health_and_s3_are_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    readiness_time = cast(ModuleType, readiness_module.__dict__["time"])
    monotonic_values = iter((0.0, 0.1, 0.2))

    def fake_monotonic() -> float:
        return next(monotonic_values)

    def fake_sleep(_seconds: float) -> None:
        calls.append("sleep")

    def fake_can_connect(_host: str, _port: int) -> bool:
        calls.append("connect")
        return len(calls) > 2

    def fake_healthcheck_responds(_health_url: str) -> bool:
        calls.append("health")
        return True

    def fake_s3_api_is_ready(_settings: AppSettings) -> bool:
        calls.append("s3")
        return True

    monkeypatch.setattr(readiness_time, "monotonic", fake_monotonic)
    monkeypatch.setattr(readiness_time, "sleep", fake_sleep)
    monkeypatch.setitem(readiness_module.__dict__, "_can_connect", fake_can_connect)
    monkeypatch.setitem(
        readiness_module.__dict__, "_healthcheck_responds", fake_healthcheck_responds
    )
    monkeypatch.setitem(readiness_module.__dict__, "_s3_api_is_ready", fake_s3_api_is_ready)

    readiness_module.wait_for_localstack_readiness(
        endpoint=harness_module.LOCALSTACK_HOST_ENDPOINT,
        timeout_seconds=1,
    )

    assert calls == ["connect", "sleep", "connect", "health", "s3"]


def test_readiness_s3_api_probe_handles_success_and_client_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings.from_env(
        harness_module.localstack_test_env(
            harness_module.new_localstack_bucket_pair(),
            endpoint=harness_module.LOCALSTACK_HOST_ENDPOINT,
            log_dir="/tmp/logs",
        )
    )
    s3_api_is_ready = cast(
        Callable[[AppSettings], bool], readiness_module.__dict__["_s3_api_is_ready"]
    )

    def build_ready_client(_settings: AppSettings) -> FakeAdminClient:
        return FakeAdminClient()

    monkeypatch.setattr(readiness_module, "localstack_admin_client", build_ready_client)
    assert s3_api_is_ready(settings) is True

    class ListBucketsErrorClient(FakeAdminClient):
        @override
        def list_buckets(self) -> object:
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "ListBuckets")

    def build_error_client(_settings: AppSettings) -> ListBucketsErrorClient:
        return ListBucketsErrorClient()

    monkeypatch.setattr(readiness_module, "localstack_admin_client", build_error_client)
    assert s3_api_is_ready(settings) is False


def test_readiness_network_probe_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    can_connect = cast(Callable[[str, int], bool], readiness_module.__dict__["_can_connect"])
    healthcheck_responds = cast(
        Callable[[str], bool], readiness_module.__dict__["_healthcheck_responds"]
    )

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        _host, port = cast(tuple[str, int], server.getsockname())
        assert can_connect("127.0.0.1", port) is True

    assert can_connect("127.0.0.1", port) is False

    class UrlOpenResponse:
        def __enter__(self) -> UrlOpenResponse:
            return self

        def __exit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _traceback: TracebackType | None,
        ) -> bool:
            return False

    def successful_urlopen(_health_url: str, *, timeout: float) -> UrlOpenResponse:
        _ = timeout
        return UrlOpenResponse()

    def failing_urlopen(_health_url: str, *, timeout: float) -> UrlOpenResponse:
        _ = timeout
        raise URLError("offline")

    monkeypatch.setattr(readiness_module, "urlopen", successful_urlopen)
    assert healthcheck_responds("http://127.0.0.1:4566/_localstack/health") is True

    monkeypatch.setattr(readiness_module, "urlopen", failing_urlopen)
    assert healthcheck_responds("http://127.0.0.1:4566/_localstack/health") is False
