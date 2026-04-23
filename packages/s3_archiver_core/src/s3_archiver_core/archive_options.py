"""Archive option parsing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from s3_archiver_core.archive_lock import parse_duration
from s3_archiver_core.archive_manifest import SourcePathFilter
from s3_archiver_core.s3 import S3TransferCapabilities


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


def cleanup_enabled_from_env(env: Mapping[str, str]) -> bool:
    """Return whether cleanup is globally enabled by ``ARCHIVER_ENABLE_CLEANUP``."""

    return env.get("ARCHIVER_ENABLE_CLEANUP", "").strip().lower() == "true"


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    value = int(env.get(key, str(default)))
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value
