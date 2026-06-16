"""Public archive manifest API."""

from __future__ import annotations

from s3_archiver_core._archive_manifest_models import (
    ArchiveGroup,
    ArchiveManifest,
    ArchiveManifestRoute,
    ArchiveManifestRouteSpec,
    CopyMode,
    ManifestEntry,
    ParserKind,
    SelectedObject,
    SkippedObject,
    SourceLister,
)
from s3_archiver_core._archive_route_manifest import (
    build_archive_manifest,
    build_route_archive_manifest,
)
from s3_archiver_core.parsers.protocol import ParserContext

__all__ = (
    "ArchiveGroup",
    "ArchiveManifest",
    "ArchiveManifestRoute",
    "ArchiveManifestRouteSpec",
    "CopyMode",
    "ManifestEntry",
    "ParserContext",
    "ParserKind",
    "SelectedObject",
    "SkippedObject",
    "SourceLister",
    "build_archive_manifest",
    "build_route_archive_manifest",
)
