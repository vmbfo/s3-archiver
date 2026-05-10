"""Health-check execution against S3 and runtime sinks."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from s3_archiver_core.errors import HealthCheckError
from s3_archiver_core.s3 import S3Client, VersioningState, build_s3_client
from s3_archiver_core.settings import AppSettings, RouteSettings, S3LocationSettings


@dataclass(frozen=True, slots=True)
class RouteHealthReport:
    """Serializable per-route health-check details."""

    name: str
    source_provider: str
    source_bucket: str
    source_endpoint_url: str
    source_path: str
    destination_provider: str
    destination_bucket: str
    destination_endpoint_url: str
    destination_path: str
    parser: str
    copy_mode: str
    source_versioning: VersioningState

    def as_dict(self) -> dict[str, str]:
        """Return one JSON-serializable route health payload."""

        return {
            "name": self.name,
            "source_provider": self.source_provider,
            "source_bucket": self.source_bucket,
            "source_endpoint_url": self.source_endpoint_url,
            "source_path": self.source_path,
            "destination_provider": self.destination_provider,
            "destination_bucket": self.destination_bucket,
            "destination_endpoint_url": self.destination_endpoint_url,
            "destination_path": self.destination_path,
            "parser": self.parser,
            "copy_mode": self.copy_mode,
            "source_versioning": self.source_versioning,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Serializable output for the check command."""

    status: str
    source_provider: str
    source_bucket: str
    source_endpoint_url: str
    source_versioning: VersioningState
    destination_provider: str
    destination_bucket: str
    destination_endpoint_url: str
    log_file: str
    checked_at: str
    route_count: int
    routes: tuple[RouteHealthReport, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-serializable health report."""

        return {
            "status": self.status,
            "provider": self.source_provider,
            "bucket": self.source_bucket,
            "endpoint_url": self.source_endpoint_url,
            "source_provider": self.source_provider,
            "source_bucket": self.source_bucket,
            "source_endpoint_url": self.source_endpoint_url,
            "source_versioning": self.source_versioning,
            "destination_provider": self.destination_provider,
            "destination_bucket": self.destination_bucket,
            "destination_endpoint_url": self.destination_endpoint_url,
            "log_file": self.log_file,
            "checked_at": self.checked_at,
            "route_count": str(self.route_count),
            "routes": [route.as_dict() for route in self.routes],
        }


def run_health_check(settings: AppSettings, log_file: Path) -> HealthReport:
    """Validate bucket access and report the current runtime shape."""

    logger = logging.getLogger("s3_archiver.health")
    routes = settings.routes
    source_endpoint_url = settings.source.resolved_endpoint_url()
    destination_endpoint_url = settings.destination.resolved_endpoint_url()
    logger.info(
        "running s3 health check",
        extra={
            "event": "health.started",
            "bucket": settings.source.bucket,
            "endpoint_url": source_endpoint_url,
            "destination_bucket": settings.destination.bucket,
            "destination_endpoint_url": destination_endpoint_url,
            "route_count": len(routes),
        },
    )
    route_reports = _check_routes(routes)
    source_versioning = route_reports[0].source_versioning
    logger.info(
        "s3 health check succeeded",
        extra={
            "event": "health.succeeded",
            "bucket": settings.source.bucket,
            "destination_bucket": settings.destination.bucket,
            "source_versioning": source_versioning,
            "route_count": len(routes),
        },
    )
    return HealthReport(
        status="ok",
        source_provider=settings.source.provider.value,
        source_bucket=settings.source.bucket,
        source_endpoint_url=source_endpoint_url,
        source_versioning=source_versioning,
        destination_provider=settings.destination.provider.value,
        destination_bucket=settings.destination.bucket,
        destination_endpoint_url=destination_endpoint_url,
        log_file=str(log_file),
        checked_at=datetime.now(tz=UTC).isoformat(),
        route_count=len(routes),
        routes=route_reports,
    )


def _check_routes(routes: tuple[RouteSettings, ...]) -> tuple[RouteHealthReport, ...]:
    reports: list[RouteHealthReport] = []
    for route in routes:
        route_source_versioning = _check_route(route)
        reports.append(_route_report(route, route_source_versioning))
    return tuple(reports)


def _check_route(route: RouteSettings) -> VersioningState:
    source_client = build_s3_client(route.source)
    _check_bucket_access(source_client, route.source, f"route {route.name!r} source")
    source_versioning = _source_versioning(source_client, route.source)
    destination_client = build_s3_client(route.destination)
    _check_bucket_access(destination_client, route.destination, f"route {route.name!r} destination")
    return source_versioning


def _route_report(route: RouteSettings, source_versioning: VersioningState) -> RouteHealthReport:
    return RouteHealthReport(
        name=route.name,
        source_provider=route.source.provider.value,
        source_bucket=route.source.bucket,
        source_endpoint_url=route.source.resolved_endpoint_url(),
        source_path=route.source.path,
        destination_provider=route.destination.provider.value,
        destination_bucket=route.destination.bucket,
        destination_endpoint_url=route.destination.resolved_endpoint_url(),
        destination_path=route.destination.path,
        parser=route.parser.value,
        copy_mode=route.copy_mode.value,
        source_versioning=source_versioning,
    )


def _check_bucket_access(client: S3Client, location: S3LocationSettings, side: str) -> None:
    try:
        _ = client.head_bucket(Bucket=location.bucket)
    except (BotoCoreError, ClientError) as exc:
        raise HealthCheckError(
            f"Failed to access {side} bucket {location.bucket!r}: {exc}"
        ) from exc


def _source_versioning(client: S3Client, location: S3LocationSettings) -> VersioningState:
    try:
        response = client.get_bucket_versioning(Bucket=location.bucket)
    except (BotoCoreError, ClientError) as exc:
        raise HealthCheckError(
            f"Failed to read source bucket versioning for {location.bucket!r}: {exc}"
        ) from exc
    status = response.get("Status")
    if status == "Enabled":
        return "Enabled"
    if status == "Suspended":
        return "Suspended"
    return "Disabled"
