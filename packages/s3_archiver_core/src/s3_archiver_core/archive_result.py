"""Archive run result models."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from s3_archiver_core._archive_manifest_models import ManifestCleanup, ManifestEntry
from s3_archiver_core.archive_manifest import ArchiveManifest


@dataclass(frozen=True, slots=True)
class ArchivePhaseResult:
    """Outcome for one archive phase."""

    phase: str
    failures: tuple[str, ...] = ()
    skipped: bool = False

    @property
    def ok(self) -> bool:
        """Return whether the phase completed without failures."""
        return self.failures == ()


@dataclass(frozen=True, slots=True)
class ArchiveRunResult:
    """Outcome for a complete archive run."""

    run_id: str
    manifest: ArchiveManifest
    copy: ArchivePhaseResult
    verify: ArchivePhaseResult
    list: ArchivePhaseResult = field(default_factory=lambda: ArchivePhaseResult("list"))

    cleanup_entries: Sequence[ManifestEntry] | None = field(default=None, compare=False)

    cleanup_store: ManifestCleanup | None = field(default=None, compare=False, repr=False)

    def close(self) -> None:
        """Release both the source manifest and the verified-entry journal."""
        self.manifest.close()
        if self.cleanup_store is not None:
            self.cleanup_store.cleanup()

    @property
    def ok(self) -> bool:
        """Return whether every archive phase completed without failures."""
        return self.list.ok and self.copy.ok and self.verify.ok
