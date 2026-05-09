"""Cleanup preview helpers for the CLI."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from s3_archiver_core.archive_manifest import ArchiveManifest, ManifestEntry, build_archive_manifest
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.errors import ArchiveRunError, S3ArchiverError
from s3_archiver_core.health import run_health_check
from s3_archiver_core.s3 import build_s3_client
from s3_archiver_core.settings import AppSettings
from s3_archiver_core.temp_files import prepare_runtime_temp_dir

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, "JsonValue"] | list["JsonValue"]

_PREVIEW_PREFIX = "cleanup-preview-"


def run_cleanup_preview(
    settings: AppSettings,
    log_file: Path,
    *,
    now: Callable[[], datetime] | None = None,
) -> dict[str, JsonValue]:
    """Build and persist the cleanup manifest without deleting any source objects."""

    clock = _utc_now if now is None else now
    started = clock()
    try:
        prepare_runtime_temp_dir(settings.temp_dir)
        _ = run_health_check(settings, log_file)
        source = S3ArchiveBucket(
            build_s3_client(settings.source),
            settings.source.bucket,
            settings.temp_dir,
        )
        options = ArchiveOptions.from_settings(settings)
        manifest = build_archive_manifest(
            source,
            run_started_at_utc=started,
            retention_days=options.retention_days,
            versioning_state=source.versioning_state(),
            source_filter=options.source_filter,
        )
    except S3ArchiverError:
        raise
    except Exception as exc:
        raise ArchiveRunError(str(exc)) from exc
    payload = _cleanup_preview_payload(settings, log_file, manifest)
    try:
        _write_cleanup_preview_file(payload, settings.temp_dir)
        return payload
    except S3ArchiverError:
        raise
    except Exception as exc:
        raise ArchiveRunError(str(exc)) from exc


def _cleanup_preview_payload(
    settings: AppSettings,
    log_file: Path,
    manifest: ArchiveManifest,
) -> dict[str, JsonValue]:
    preview: dict[str, JsonValue] = {
        "cleanup_enabled_in_settings": settings.cleanup_enabled,
        "object_count": len(manifest.entries),
        "run_started_at_utc": manifest.run_started_at_utc.isoformat(),
        "retention_cutoff_utc": manifest.retention_cutoff_utc.isoformat(),
        "entries": [_manifest_entry_payload(entry) for entry in manifest.entries],
    }
    return {
        "status": "ok",
        "source_bucket": settings.source.bucket,
        "destination_bucket": settings.destination.bucket,
        "log_file": str(log_file),
        "cleanup_preview": preview,
    }


def _write_cleanup_preview_file(payload: dict[str, JsonValue], temp_dir: Path) -> None:
    preview_path = temp_dir / f"{_PREVIEW_PREFIX}{uuid4().hex}.json"
    preview = cast(dict[str, JsonValue], payload["cleanup_preview"]).copy()
    preview["manifest_file"] = str(preview_path)
    payload["cleanup_preview"] = preview
    _ = preview_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _manifest_entry_payload(entry: ManifestEntry) -> dict[str, JsonValue]:
    return {
        "source_bucket": entry.source_bucket,
        "key": entry.key,
        "version_id": entry.version_id,
        "size": entry.size,
        "etag": entry.etag,
        "last_modified_utc": entry.last_modified.isoformat(),
    }


def _utc_now() -> datetime:
    """Return the current UTC timestamp."""

    return datetime.now(tz=UTC)
