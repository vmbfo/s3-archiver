"""Archive option parsing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from s3_archiver_core.archive_lock import parse_duration
from s3_archiver_core.archive_manifest import SourcePathFilter
from s3_archiver_core.s3 import S3TransferCapabilities
from s3_archiver_core.settings import AppSettings


@dataclass(frozen=True, slots=True)
class ArchiveOptions:
    """Archive workflow settings with conservative defaults."""

    retention_days: int
    cleanup_enabled: bool = False
    max_workers: int = 1
    run_timeout: timedelta = timedelta(days=7)
    source_filter: SourcePathFilter = field(default_factory=SourcePathFilter)
    transfer_capabilities: S3TransferCapabilities = field(default_factory=S3TransferCapabilities)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ArchiveOptions:
        """Build archive options from archive-specific environment variables."""

        return cls(
            retention_days=_positive_int(env, "ARCHIVER_RETENTION_DAYS", 60),
            cleanup_enabled=cleanup_enabled_from_env(env),
            max_workers=_positive_int(env, "ARCHIVER_MAX_WORKERS", 16),
            run_timeout=parse_duration(env.get("ARCHIVER_RUN_TIMEOUT", "7d")),
        )

    @classmethod
    def from_settings(cls, settings: AppSettings) -> ArchiveOptions:
        """Build archive options from validated application settings."""

        return cls(
            retention_days=settings.retention_days,
            cleanup_enabled=settings.cleanup_enabled,
            max_workers=settings.max_workers,
            run_timeout=settings.run_timeout,
            source_filter=_source_filter(settings),
            transfer_capabilities=_transfer_capabilities(settings),
        )


def cleanup_enabled_from_env(env: Mapping[str, str]) -> bool:
    """Return whether cleanup is globally enabled by ``ARCHIVER_ENABLE_CLEANUP``."""

    return env.get("ARCHIVER_ENABLE_CLEANUP", "").strip().lower() == "true"


def _source_filter(settings: AppSettings) -> SourcePathFilter:
    filters = settings.path_filters
    if filters.whitelist_enabled:
        return SourcePathFilter("whitelist", filters.whitelist)
    if filters.blacklist_enabled:
        return SourcePathFilter("blacklist", filters.blacklist)
    return SourcePathFilter()


def _transfer_capabilities(settings: AppSettings) -> S3TransferCapabilities:
    source = settings.source.storage_identity()
    destination = settings.destination.storage_identity()
    native_copy = (
        source.provider == destination.provider
        and source.endpoint_url == destination.endpoint_url
        and source.namespace == destination.namespace
    )
    return S3TransferCapabilities(native_copy=native_copy, multipart_copy=native_copy)


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    value = int(env.get(key, str(default)))
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value
