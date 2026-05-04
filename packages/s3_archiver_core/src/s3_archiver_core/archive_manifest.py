from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from s3_archiver_core._archive_manifest_models import (
    DEFAULT_SOURCE_FILTER,
    ArchiveGroup,
    ArchiveManifest,
    ArchiveManifestRoute,
    CopyMode,
    DestinationLocator,
    ManifestEntry,
    ParserKind,
    ParserSelector,
    SelectedObject,
    SkippedObject,
    SourceLister,
    SourcePathFilter,
)
from s3_archiver_core.archive_timestamp import (
    TimestampSource,
    archive_root_for_key,
    destination_archive_key,
    select_key_timestamp,
)
from s3_archiver_core.s3 import VersioningState

__all__ = (
    "ArchiveGroup",
    "ArchiveManifest",
    "ArchiveManifestRoute",
    "CopyMode",
    "ManifestEntry",
    "ParserKind",
    "SelectedObject",
    "SkippedObject",
    "SourceLister",
    "SourcePathFilter",
    "TimestampSource",
    "archive_root_for_key",
    "build_archive_manifest",
    "build_route_archive_manifest",
    "destination_archive_key",
    "select_key_timestamp",
)


def build_archive_manifest(
    source: SourceLister,
    *,
    run_started_at_utc: datetime,
    retention_days: int | None = None,
    versioning_state: VersioningState,
    source_filter: SourcePathFilter = DEFAULT_SOURCE_FILTER,
    route_name: str = "default",
    parser_kind: ParserKind = "filename_timestamp",
    copy_mode: CopyMode = "daily_tar_gz",
    source_path: str = "",
    destination: DestinationLocator | None = None,
    destination_path: str = "",
    parser: ParserSelector | None = None,
    source_identity: object | None = None,
    destination_identity: object | None = None,
) -> ArchiveManifest:
    """Build an archive manifest from source object keys."""

    from s3_archiver_core._archive_manifest_builder import build_archive_manifest as _build

    return _build(
        source,
        run_started_at_utc=run_started_at_utc,
        retention_days=retention_days,
        versioning_state=versioning_state,
        source_filter=source_filter,
        route_name=route_name,
        parser_kind=parser_kind,
        copy_mode=copy_mode,
        source_path=source_path,
        destination=destination,
        destination_path=destination_path,
        parser=parser,
        source_identity=source_identity,
        destination_identity=destination_identity,
    )


def build_route_archive_manifest(
    routes: Iterable[ArchiveManifestRoute],
    *,
    run_started_at_utc: datetime,
) -> ArchiveManifest:
    """Build a deterministic global manifest for route-based archiving."""

    from s3_archiver_core._archive_route_manifest import build_route_archive_manifest as _build

    return _build(routes, run_started_at_utc=run_started_at_utc)
