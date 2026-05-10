from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from s3_archiver_core._archive_manifest_models import (
    ArchiveGroup,
    ArchiveManifest,
    ArchiveManifestRoute,
    CopyMode,
    DestinationLocator,
    ManifestEntry,
    ParserKind,
    ParserResult,
    ParserSelector,
    SelectedObject,
    SkippedObject,
    SourceLister,
)
from s3_archiver_core.parsers.protocol import ParserContext
from s3_archiver_core.s3 import VersioningState

__all__ = (
    "ArchiveGroup",
    "ArchiveManifest",
    "ArchiveManifestRoute",
    "CopyMode",
    "ManifestEntry",
    "ParserContext",
    "ParserKind",
    "ParserResult",
    "ParserSelector",
    "SelectedObject",
    "SkippedObject",
    "SourceLister",
    "build_archive_manifest",
    "build_route_archive_manifest",
)


def build_archive_manifest(
    source: SourceLister,
    *,
    run_started_at_utc: datetime,
    versioning_state: VersioningState,
    parser_kind: ParserKind,
    copy_mode: CopyMode,
    route_name: str = "default",
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
        versioning_state=versioning_state,
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
