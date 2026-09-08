from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path

from s3_archiver_core._archive_manifest_models import ArchiveGroup
from s3_archiver_core._archive_protocols import ArchiveBucket
from s3_archiver_core._archive_size_limits import estimated_archive_size_bytes
from s3_archiver_core.archive_group_metadata import (
    ARCHIVE_SHA256_METADATA_KEY,
    ARCHIVE_SIZE_METADATA_KEY,
    MANIFEST_SHA256_METADATA_KEY,
    existing_archive_refreshable,
    existing_archive_verified,
    group_metadata,
    uploaded_archive_verified,
)
from s3_archiver_core.archive_membership import MEMBERSHIP_METADATA_KEY, upload_membership
from s3_archiver_core.archive_routes import DebugLogger
from s3_archiver_core.archive_tar import write_tar_gz_archive
from s3_archiver_core.s3 import S3ObjectProperties
from s3_archiver_core.temp_files import TRANSFER_TEMP_PREFIX, ensure_temp_storage_available

type ProgressAdvance = Callable[[int], None]


def copy_group(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    group: ArchiveGroup,
    debug_logger: DebugLogger | None,
    *,
    progress_logger: ProgressAdvance | None = None,
) -> tuple[str | None, bool]:
    destination_key = group.destination_archive_key
    try:
        return _copy_group_checked(
            source, destination, group, debug_logger, progress_logger=progress_logger
        )
    except Exception as exc:
        return f"{destination_key}: {exc}", False


def _copy_group_checked(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    group: ArchiveGroup,
    debug_logger: DebugLogger | None,
    *,
    progress_logger: ProgressAdvance | None,
) -> tuple[str | None, bool]:
    destination_key = group.destination_archive_key
    metadata = group_metadata(group)
    existing = destination.head_object(destination_key)
    if existing is not None:
        if archive_size_matches(existing) and existing_archive_verified(
            destination, destination_key, existing.metadata, metadata
        ):
            _advance_group_progress(group, progress_logger)
            return None, True
        if not existing_archive_refreshable(
            existing.metadata, metadata, destination=destination, group=group
        ):
            return f"{destination_key}: archive verification failed", False
    archive_path: Path | None = None
    try:
        if debug_logger is not None:
            for entry in group.entries:
                debug_logger(entry, "deterministic_tar_gzip")
        _ = ensure_temp_storage_available(
            destination.temp_dir,
            required_bytes=estimated_archive_size_bytes(group.entries),
            source_key=_group_source_key(group),
            destination_key=destination_key,
            operation="archive_group_staging",
        )
        destination.temp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "wb", delete=False, dir=destination.temp_dir, prefix=TRANSFER_TEMP_PREFIX
        ) as archive_file:
            archive_path = Path(archive_file.name)
        archive_sha256 = write_tar_gz_archive(
            source,
            group,
            archive_path,
            progress_logger=None if progress_logger is None else lambda: progress_logger(1),
        )
        upload_metadata = dict(metadata)
        upload_metadata[ARCHIVE_SHA256_METADATA_KEY] = archive_sha256
        archive_size = archive_path.stat().st_size
        upload_metadata[ARCHIVE_SIZE_METADATA_KEY] = str(archive_size)
        upload_metadata[MEMBERSHIP_METADATA_KEY] = upload_membership(
            destination, group, metadata[MANIFEST_SHA256_METADATA_KEY]
        )
        destination.upload_archive_file(destination_key, archive_path, upload_metadata)
    except Exception as exc:
        return f"{destination_key}: {exc}", False
    finally:
        if archive_path is not None:  # pragma: no branch
            archive_path.unlink(missing_ok=True)
    verified = destination.head_object(destination_key)
    if verified is None:
        return f"{destination_key}: destination missing", False
    if verified.size != archive_size:
        return f"{destination_key}: archive size mismatch", False
    if uploaded_archive_verified(destination, destination_key, verified.metadata, upload_metadata):
        return None, True
    return f"{destination_key}: archive verification failed", False


def _advance_group_progress(group: ArchiveGroup, progress_logger: ProgressAdvance | None) -> None:
    if progress_logger is None:
        return
    progress_logger(group.source_count or len(group.entries))


def _group_source_key(group: ArchiveGroup) -> str:
    if group.entries:
        return group.entries[0].key
    return "<empty archive group>"


def archive_size_matches(properties: S3ObjectProperties) -> bool:
    recorded = properties.metadata.get(ARCHIVE_SIZE_METADATA_KEY)
    return recorded is None or recorded == str(properties.size)
