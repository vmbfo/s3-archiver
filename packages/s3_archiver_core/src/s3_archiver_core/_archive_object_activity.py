"""Per-object archive activity logging."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import date
from threading import Event, Timer

from s3_archiver_core._archive_manifest_models import ManifestEntry
from s3_archiver_core.archive_group_metadata import (
    ARCHIVE_SHA256_METADATA_KEY,
    MANIFEST_SHA256_METADATA_KEY,
    SOURCE_COUNT_METADATA_KEY,
)

_LOGGER = logging.getLogger("s3_archiver.archive")
_LONG_OBJECT_LOG_SECONDS_ENV = "ARCHIVER_LONG_OBJECT_LOG_SECONDS"
_LARGE_OBJECT_LOG_BYTES_ENV = "ARCHIVER_LARGE_OBJECT_LOG_BYTES"
_DEFAULT_LONG_OBJECT_LOG_SECONDS = 300.0
_DEFAULT_LARGE_OBJECT_LOG_BYTES = 1024 * 1024 * 1024


def log_large_object(
    *,
    operation: str,
    source_bucket: str,
    source_key: str,
    source_version_id: str | None,
    destination_bucket: str,
    destination_key: str,
    size_bytes: int,
) -> None:
    """Log an object that is large enough to explain slow archive progress."""

    threshold = _large_object_log_bytes()
    if threshold <= 0 or size_bytes < threshold:
        return
    _LOGGER.info(
        "archive %s large object source_key=%s size_bytes=%d destination_key=%s",
        operation,
        source_key,
        size_bytes,
        destination_key,
        extra={
            **_object_context(
                operation=operation,
                source_bucket=source_bucket,
                source_key=source_key,
                source_version_id=source_version_id,
                destination_bucket=destination_bucket,
                destination_key=destination_key,
                size_bytes=size_bytes,
            ),
            "event": "archive.object.large",
            "large_object_threshold_bytes": threshold,
        },
    )


def log_large_entry(
    *, operation: str, entry: ManifestEntry, destination_bucket: str, destination_key: str
) -> None:
    """Log a large manifest entry when it crosses the configured threshold."""

    log_large_object(
        operation=operation,
        source_bucket=entry.source_bucket,
        source_key=entry.key,
        source_version_id=entry.version_id,
        destination_bucket=destination_bucket,
        destination_key=destination_key,
        size_bytes=entry.object.properties.size,
    )


@contextmanager
def entry_activity_watchdog(
    *, operation: str, entry: ManifestEntry, destination_bucket: str, destination_key: str
) -> Iterator[None]:
    """Watch one manifest entry operation for long-running activity."""

    with object_activity_watchdog(
        operation=operation,
        source_bucket=entry.source_bucket,
        source_key=entry.key,
        source_version_id=entry.version_id,
        destination_bucket=destination_bucket,
        destination_key=destination_key,
        size_bytes=entry.object.properties.size,
    ):
        yield


@contextmanager
def object_activity_watchdog(
    *,
    operation: str,
    source_bucket: str,
    source_key: str,
    source_version_id: str | None,
    destination_bucket: str,
    destination_key: str,
    size_bytes: int,
) -> Iterator[None]:
    """Log the object key if a single object operation runs for too long."""

    threshold = _long_object_log_seconds()
    if threshold <= 0:
        yield
        return

    done = Event()
    started = time.monotonic()

    def emit() -> None:
        if done.is_set():
            return
        elapsed_seconds = max(time.monotonic() - started, threshold)
        _LOGGER.info(
            ("archive %s still running source_key=%s elapsed_seconds=%.3f destination_key=%s"),
            operation,
            source_key,
            elapsed_seconds,
            destination_key,
            extra={
                **_object_context(
                    operation=operation,
                    source_bucket=source_bucket,
                    source_key=source_key,
                    source_version_id=source_version_id,
                    destination_bucket=destination_bucket,
                    destination_key=destination_key,
                    size_bytes=size_bytes,
                ),
                "event": "archive.object.long_running",
                "elapsed_seconds": round(elapsed_seconds, 3),
                "long_object_log_seconds": threshold,
            },
        )

    timer = Timer(threshold, emit)
    timer.daemon = True
    timer.start()
    try:
        yield
    finally:
        done.set()
        timer.cancel()


def _object_context(
    *,
    operation: str,
    source_bucket: str,
    source_key: str,
    source_version_id: str | None,
    destination_bucket: str,
    destination_key: str,
    size_bytes: int,
) -> dict[str, str | int | None]:
    return {
        "operation": operation,
        "phase": "copy",
        "source_bucket": source_bucket,
        "source_key": source_key,
        "source_version_id": source_version_id,
        "destination_bucket": destination_bucket,
        "destination_key": destination_key,
        "size_bytes": size_bytes,
    }


def log_in_progress_day_overwrite(
    *,
    destination_bucket: str,
    destination_key: str,
    target_day: date,
    existing_metadata: Mapping[str, str],
    expected_metadata: Mapping[str, str],
) -> None:
    """Record a structured warning when overwriting today's archive on mismatch.

    Today's archive contains only objects with parser timestamps up to the run
    start, so a later run with newer source objects legitimately produces a
    different manifest. Replace the stale archive instead of failing the run.
    """

    _LOGGER.warning(
        "archive overwrite in-progress day destination_key=%s target_day=%s",
        destination_key,
        target_day.isoformat(),
        extra={
            "event": "archive.copy.overwrite_in_progress_day",
            "destination_bucket": destination_bucket,
            "destination_key": destination_key,
            "target_day": target_day.isoformat(),
            "existing_manifest_sha256": existing_metadata.get(MANIFEST_SHA256_METADATA_KEY),
            "existing_source_count": existing_metadata.get(SOURCE_COUNT_METADATA_KEY),
            "existing_archive_sha256": existing_metadata.get(ARCHIVE_SHA256_METADATA_KEY),
            "expected_manifest_sha256": expected_metadata.get(MANIFEST_SHA256_METADATA_KEY),
            "expected_source_count": expected_metadata.get(SOURCE_COUNT_METADATA_KEY),
        },
    )


def _large_object_log_bytes() -> int:
    return _int_env(_LARGE_OBJECT_LOG_BYTES_ENV, _DEFAULT_LARGE_OBJECT_LOG_BYTES)


def _long_object_log_seconds() -> float:
    return float(_int_env(_LONG_OBJECT_LOG_SECONDS_ENV, int(_DEFAULT_LONG_OBJECT_LOG_SECONDS)))


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
