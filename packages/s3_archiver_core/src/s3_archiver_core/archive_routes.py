"""Archive route construction helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from s3_archiver_core._archive_s3_protocols import ArchiveS3Client
from s3_archiver_core.archive import ArchiveRoute
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.s3 import transfer_capabilities_for_locations
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
            parser_kind=route.parser.value,
            copy_mode=route.copy_mode.value,
            source_path=route.source.path,
            destination_path=route.destination.path,
            source_identity=route.source.storage_identity(),
            destination_identity=route.destination.storage_identity(),
            transfer_capabilities=transfer_capabilities_for_locations(
                route.source, route.destination
            ),
        )
        for route in settings.routes
    )
