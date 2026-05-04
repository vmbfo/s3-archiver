from __future__ import annotations

from collections.abc import Callable
from typing import cast

from s3_archiver_core._archive_s3_protocols import ArchiveS3Client
from s3_archiver_core.archive import ArchiveRoute
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.settings import AppSettings, S3LocationSettings

BuildS3Client = Callable[[S3LocationSettings], object]


def archive_routes_from_settings(
    settings: AppSettings, build_client: BuildS3Client
) -> tuple[ArchiveRoute, ...]:
    """Build runtime archive route adapters from validated settings."""

    return tuple(
        ArchiveRoute(
            route.name,
            S3ArchiveBucket(
                cast(ArchiveS3Client, build_client(route.source)),
                route.source.bucket,
                settings.temp_dir,
            ),
            S3ArchiveBucket(
                cast(ArchiveS3Client, build_client(route.destination)),
                route.destination.bucket,
                settings.temp_dir,
            ),
            route.source.path,
            route.destination.path,
            route.parser.value,
            route.copy_mode.value,
        )
        for route in settings.routes
    )
