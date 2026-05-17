"""Archive route runtime models and construction helpers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass, field
from typing import cast

from s3_archiver_core._archive_manifest_models import CopyMode, ManifestEntry, ParserKind
from s3_archiver_core._archive_protocols import ArchiveBucket
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.s3 import (
    S3Client,
    S3TransferCapabilities,
    VersioningState,
    transfer_capabilities_for_locations,
)
from s3_archiver_core.settings import AppSettings, S3LocationSettings

DebugLogger = Callable[[ManifestEntry, str], None]
BuildS3Client = Callable[[S3LocationSettings], object]


@dataclass(frozen=True, slots=True)
class ArchiveRoute:
    """Runtime source/destination pair for one configured archive route."""

    name: str
    source: ArchiveBucket
    destination: ArchiveBucket
    _: KW_ONLY
    parser_kind: ParserKind
    copy_mode: CopyMode
    source_path: str = ""
    destination_path: str = ""
    copy_mode_group_after_timestamp_parts: int = 0
    versioning_state: VersioningState | None = None
    source_identity: object | None = None
    destination_identity: object | None = None
    transfer_capabilities: S3TransferCapabilities = field(default_factory=S3TransferCapabilities)


def archive_routes_from_settings(
    settings: AppSettings, build_client: BuildS3Client
) -> tuple[ArchiveRoute, ...]:
    """Build runtime archive route adapters from validated settings."""

    return tuple(
        ArchiveRoute(
            route.name,
            S3ArchiveBucket(
                cast(S3Client, build_client(route.source)),
                route.source.bucket,
                settings.temp_dir,
            ),
            S3ArchiveBucket(
                cast(S3Client, build_client(route.destination)),
                route.destination.bucket,
                settings.temp_dir,
            ),
            parser_kind=route.parser.value,
            copy_mode=route.copy_mode.value,
            source_path=route.source.path,
            destination_path=route.destination.path,
            copy_mode_group_after_timestamp_parts=route.copy_mode_group_after_timestamp_parts,
            source_identity=route.source.storage_identity(),
            destination_identity=route.destination.storage_identity(),
            transfer_capabilities=transfer_capabilities_for_locations(
                route.source, route.destination
            ),
        )
        for route in settings.routes
    )
