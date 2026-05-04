"""Health-check execution against S3 and runtime sinks."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from s3_archiver_core.errors import HealthCheckError
from s3_archiver_core.s3 import S3Client, VersioningState, build_s3_client
from s3_archiver_core.settings import AppSettings, RouteSettings, S3LocationSettings


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

    def as_dict(self) -> dict[str, str]:
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
    source_versioning = _check_routes(routes)
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
    )


def _check_routes(routes: tuple[RouteSettings, ...]) -> VersioningState:
    source_versioning: VersioningState | None = None
    for route in routes:
        route_source_versioning = _check_route(route)
        if source_versioning is None:
            source_versioning = route_source_versioning
    assert source_versioning is not None
    return source_versioning


def _check_route(route: RouteSettings) -> VersioningState:
    source_client = build_s3_client(route.source)
    _check_bucket_access(source_client, route.source, f"route {route.name!r} source")
    source_versioning = _source_versioning(source_client, route.source)
    destination_client = build_s3_client(route.destination)
    _check_bucket_access(destination_client, route.destination, f"route {route.name!r} destination")
    return source_versioning


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
