"""Tests for health-check execution."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from mypy_boto3_s3.client import S3Client
from s3_archiver_core.errors import HealthCheckError
from s3_archiver_core.health import run_health_check
from s3_archiver_core.settings import AppSettings, S3LocationSettings


class SuccessfulClient:
    """Minimal client double for successful requests."""

    called_bucket: str | None = None
    _versioning_status: str | None

    def __init__(self, versioning_status: str | None = "Enabled") -> None:
        self._versioning_status = versioning_status

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        self.called_bucket = Bucket

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:  # noqa: N803
        self.called_bucket = Bucket
        if self._versioning_status is None:
            return {}
        return {"Status": self._versioning_status}


class AuthFailingClient:
    """Minimal client double for authentication failures."""

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        _ = Bucket
        raise ClientError({"Error": {"Code": "403", "Message": "denied"}}, "HeadBucket")

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:  # noqa: N803
        _ = Bucket
        return {"Status": "Enabled"}


class ConnectivityFailingClient:
    """Minimal client double for connectivity failures."""

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        _ = Bucket
        raise EndpointConnectionError(endpoint_url="http://localstack:4566")

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:  # noqa: N803
        _ = Bucket
        return {"Status": "Enabled"}


class VersioningFailingClient:
    """Minimal client double for source versioning failures."""

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        _ = Bucket

    def get_bucket_versioning(self, *, Bucket: str) -> dict[str, str]:  # noqa: N803
        _ = Bucket
        raise ClientError({"Error": {"Code": "403", "Message": "denied"}}, "GetBucketVersioning")


@pytest.mark.unit()
def test_run_health_check_reports_success(
    monkeypatch: pytest.MonkeyPatch, base_env: dict[str, str]
) -> None:
    settings = AppSettings.from_env(base_env)
    clients = [SuccessfulClient(), SuccessfulClient()]

    def build_client(_: S3LocationSettings) -> S3Client:
        return cast(S3Client, cast(object, clients.pop(0)))

    monkeypatch.setattr(
        "s3_archiver_core.health.build_s3_client",
        build_client,
    )

    report = run_health_check(settings, Path(base_env["LOG_DIR"]) / "s3-archiver.log")

    assert report.source_bucket == "archive-bucket"
    assert report.destination_bucket == "destination-bucket"
    assert report.source_versioning == "Enabled"
    assert clients == []
    assert report.status == "ok"
    assert report.route_count == 1


@pytest.mark.unit()
@pytest.mark.parametrize(
    ("raw_status", "expected_status"),
    [
        ("Suspended", "Suspended"),
        (None, "Disabled"),
    ],
)
def test_run_health_check_reports_non_enabled_source_versioning_states(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
    raw_status: str | None,
    expected_status: str,
) -> None:
    settings = AppSettings.from_env(base_env)
    clients = [SuccessfulClient(raw_status), SuccessfulClient()]

    def build_client(_: S3LocationSettings) -> S3Client:
        return cast(S3Client, cast(object, clients.pop(0)))

    monkeypatch.setattr(
        "s3_archiver_core.health.build_s3_client",
        build_client,
    )

    report = run_health_check(settings, Path(base_env["LOG_DIR"]) / "s3-archiver.log")

    assert report.source_versioning == expected_status


@pytest.mark.unit()
def test_run_health_check_validates_all_configured_routes(
    monkeypatch: pytest.MonkeyPatch, base_env: dict[str, str]
) -> None:
    env = _multi_route_env(base_env)
    settings = AppSettings.from_env(env)
    clients = [
        SuccessfulClient(),
        SuccessfulClient(),
        SuccessfulClient("Suspended"),
        SuccessfulClient(),
    ]
    built_buckets: list[str] = []

    def build_client(location: S3LocationSettings) -> S3Client:
        built_buckets.append(location.bucket)
        return cast(S3Client, cast(object, clients.pop(0)))

    monkeypatch.setattr(
        "s3_archiver_core.health.build_s3_client",
        build_client,
    )

    report = run_health_check(settings, Path(env["LOG_DIR"]) / "s3-archiver.log")

    assert report.source_versioning == "Enabled"
    assert report.route_count == 2
    assert built_buckets == [
        "archive-bucket",
        "destination-bucket",
        "second-source-bucket",
        "second-destination-bucket",
    ]
    assert clients == []


@pytest.mark.unit()
def test_run_health_check_raises_for_later_route_source_access_error(
    monkeypatch: pytest.MonkeyPatch, base_env: dict[str, str]
) -> None:
    env = _multi_route_env(base_env)
    settings = AppSettings.from_env(env)
    clients = [SuccessfulClient(), SuccessfulClient(), AuthFailingClient()]

    def build_client(_: S3LocationSettings) -> S3Client:
        return cast(S3Client, cast(object, clients.pop(0)))

    monkeypatch.setattr(
        "s3_archiver_core.health.build_s3_client",
        build_client,
    )

    with pytest.raises(HealthCheckError, match="route 'secondary' source bucket"):
        _ = run_health_check(settings, Path(env["LOG_DIR"]) / "s3-archiver.log")


@pytest.mark.unit()
def test_run_health_check_raises_on_auth_error(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)

    def build_client(_: S3LocationSettings) -> S3Client:
        return cast(S3Client, cast(object, AuthFailingClient()))

    monkeypatch.setattr(
        "s3_archiver_core.health.build_s3_client",
        build_client,
    )

    with pytest.raises(HealthCheckError, match="denied"):
        _ = run_health_check(settings, Path(base_env["LOG_DIR"]) / "s3-archiver.log")


@pytest.mark.unit()
def test_run_health_check_raises_on_connectivity_error(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)

    def build_client(_: S3LocationSettings) -> S3Client:
        return cast(S3Client, cast(object, ConnectivityFailingClient()))

    monkeypatch.setattr(
        "s3_archiver_core.health.build_s3_client",
        build_client,
    )

    with pytest.raises(HealthCheckError, match="Could not connect"):
        _ = run_health_check(settings, Path(base_env["LOG_DIR"]) / "s3-archiver.log")


@pytest.mark.unit()
def test_run_health_check_raises_on_destination_access_error(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)
    clients = [SuccessfulClient(), AuthFailingClient()]

    def build_client(_: S3LocationSettings) -> S3Client:
        return cast(S3Client, cast(object, clients.pop(0)))

    monkeypatch.setattr(
        "s3_archiver_core.health.build_s3_client",
        build_client,
    )

    with pytest.raises(HealthCheckError, match="destination bucket"):
        _ = run_health_check(settings, Path(base_env["LOG_DIR"]) / "s3-archiver.log")


@pytest.mark.unit()
def test_run_health_check_raises_on_source_versioning_error(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)
    clients = [VersioningFailingClient(), SuccessfulClient()]

    def build_client(_: S3LocationSettings) -> S3Client:
        return cast(S3Client, cast(object, clients.pop(0)))

    monkeypatch.setattr(
        "s3_archiver_core.health.build_s3_client",
        build_client,
    )

    with pytest.raises(HealthCheckError, match="source bucket versioning"):
        _ = run_health_check(settings, Path(base_env["LOG_DIR"]) / "s3-archiver.log")


def _multi_route_env(env: dict[str, str]) -> dict[str, str]:
    updated = dict(env)
    routes = cast(list[dict[str, object]], json.loads(updated["ARCHIVER_CONFIG_JSON"]))
    first = routes[0]
    second = deepcopy(first)
    second["name"] = "secondary"
    second_source = cast(dict[str, object], second["source"])
    second_source["bucket"] = "second-source-bucket"
    second_destination = cast(dict[str, object], second["destination"])
    second_destination["bucket"] = "second-destination-bucket"
    routes.append(second)
    updated["ARCHIVER_CONFIG_JSON"] = json.dumps(routes)
    return updated
