from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from s3_archiver_core._archive_manifest_models import CopyMode, ManifestEntry, ParserKind
from s3_archiver_core._archive_protocols import ArchiveBucket
from s3_archiver_core.s3 import S3TransferCapabilities

DebugLogger = Callable[[ManifestEntry, str], None]


@dataclass(frozen=True, slots=True)
class ArchiveRoute:
    """Runtime source/destination pair for one configured archive route."""

    name: str
    source: ArchiveBucket
    destination: ArchiveBucket
    source_path: str = ""
    destination_path: str = ""
    parser_kind: ParserKind = "filename_timestamp"
    copy_mode: CopyMode = "daily_tar_gz"
    source_identity: object | None = None
    destination_identity: object | None = None
    transfer_capabilities: S3TransferCapabilities = field(default_factory=S3TransferCapabilities)
