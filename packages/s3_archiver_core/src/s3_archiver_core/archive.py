from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from s3_archiver_core._archive_protocols import ArchiveBucket, ArchiveRunLock
from s3_archiver_core.archive_manifest import (
    ArchiveManifest,
    ManifestEntry,
    build_archive_manifest,
)
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_transfer import (
    VerificationResult,
    archive_metadata,
    recover_archived_entry,
    select_transfer_strategy,
    verify_destination,
    verify_destination_content,
    verify_source_unchanged,
)
from s3_archiver_core.archive_workers import run_archive_workers

# fmt: off
__all__ = ("ArchiveBucket", "ArchivePhaseResult", "ArchiveRunLock", "ArchiveRunResult", "DebugLogger", "run_archive")  # noqa: E501
# fmt: on


@dataclass(frozen=True, slots=True)
class ArchivePhaseResult:
    """Phase outcome for archive reporting."""

    phase: str
    failures: tuple[str, ...] = ()
    skipped: bool = False

    @property
    def ok(self) -> bool:
        """Return whether the phase completed successfully."""

        return self.failures == ()


@dataclass(frozen=True, slots=True)
class ArchiveRunResult:
    """Archive run outcome."""

    run_id: str
    manifest: ArchiveManifest
    copy: ArchivePhaseResult
    verify: ArchivePhaseResult
    cleanup: ArchivePhaseResult
    list: ArchivePhaseResult = field(default_factory=lambda: ArchivePhaseResult("list"))

    @property
    def ok(self) -> bool:
        """Return whether all executed phases succeeded."""

        return self.list.ok and self.copy.ok and self.verify.ok and self.cleanup.ok


DebugLogger = Callable[[ManifestEntry, str], None]


def run_archive(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    options: ArchiveOptions,
    *,
    run_started_at_utc: datetime | None = None,
    run_lock: ArchiveRunLock | None = None,
    debug_logger: DebugLogger | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ArchiveRunResult:
    """Run strict list, copy, verify, cleanup phases."""

    now = clock or (lambda: datetime.now(tz=UTC))
    started = run_started_at_utc or now()
    deadline = started + options.run_timeout
    run_id = uuid4().hex
    if run_lock is not None and not run_lock.acquire(
        run_id=run_id, run_started_at_utc=started, timeout=options.run_timeout
    ):
        raise RuntimeError("archive run lock is already held")
    try:
        try:
            manifest = build_archive_manifest(
                source,
                run_started_at_utc=started,
                retention_days=options.retention_days,
                versioning_state=source.versioning_state(),
                source_filter=options.source_filter,
            )
        except Exception as exc:
            return ArchiveRunResult(
                run_id,
                _empty_manifest(started, options),
                _skipped("copy"),
                _skipped("verify"),
                _skipped("cleanup"),
                ArchivePhaseResult("list", (str(exc),)),
            )
        if _timed_out(now, deadline):
            return ArchiveRunResult(
                run_id, manifest, _timeout("copy"), _skipped("verify"), _skipped("cleanup")
            )

        def timeout() -> bool:
            return _timed_out(now, deadline)

        def time_remaining() -> float:
            return max((deadline - now()).total_seconds(), 0.0)

        copy_result, phase_entries = _copy_phase(
            source, destination, options, manifest.entries, debug_logger, timeout, time_remaining
        )
        if _timed_out(now, deadline):
            return ArchiveRunResult(
                run_id, manifest, _timeout("copy"), _skipped("verify"), _skipped("cleanup")
            )
        verify_result = (
            _skipped("verify")
            if not copy_result.ok
            else _verify_phase(source, destination, options, phase_entries, timeout, time_remaining)
        )
        if copy_result.ok and _timed_out(now, deadline):
            return ArchiveRunResult(
                run_id, manifest, copy_result, _timeout("verify"), _skipped("cleanup")
            )
        cleanup_result = (
            _cleanup_phase(source, options, phase_entries, timeout, time_remaining)
            if copy_result.ok and verify_result.ok
            else _skipped("cleanup")
        )
        if copy_result.ok and verify_result.ok and _timed_out(now, deadline):
            cleanup_result = _timeout("cleanup")
        return ArchiveRunResult(run_id, manifest, copy_result, verify_result, cleanup_result)
    finally:
        if run_lock is not None:
            run_lock.release(run_id=run_id)


def _copy_phase(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    options: ArchiveOptions,
    entries: tuple[ManifestEntry, ...],
    debug_logger: DebugLogger | None,
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> tuple[ArchivePhaseResult, tuple[ManifestEntry, ...]]:
    recovered: dict[str, ManifestEntry] = {}

    def worker(entry: ManifestEntry) -> str | None:
        failure, effective = _copy_one(source, destination, options, entry, debug_logger)
        if effective != entry:
            recovered[entry.key] = effective
        return failure

    phase = ArchivePhaseResult(
        "copy",
        run_archive_workers(entries, options.max_workers, worker, timed_out, time_remaining),
    )
    return phase, tuple(recovered.get(entry.key, entry) for entry in entries)


def _copy_one(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    options: ArchiveOptions,
    entry: ManifestEntry,
    debug_logger: DebugLogger | None,
) -> tuple[str | None, ManifestEntry]:
    existing = destination.head_object(entry.key)
    if existing is not None:
        effective = recover_archived_entry(
            entry, existing, lambda version_id: source.head_object(entry.key, version_id)
        )
        verified = _verify_archive_copy(source, destination, effective)
        return (None if verified.ok else f"{entry.key}: {verified.detail}", effective)
    strategy = select_transfer_strategy(entry.size, options.transfer_capabilities)
    if debug_logger is not None:
        debug_logger(entry, strategy)
    destination.copy_from(
        source,
        entry.source_bucket,
        entry.key,
        entry.version_id,
        entry.object.properties,
        entry.key,
        archive_metadata(entry),
        strategy,
    )
    return None, entry


def _verify_phase(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    options: ArchiveOptions,
    entries: tuple[ManifestEntry, ...],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> ArchivePhaseResult:
    def worker(entry: ManifestEntry) -> str | None:
        return _verify_one(source, destination, entry)

    return ArchivePhaseResult(
        "verify",
        run_archive_workers(entries, options.max_workers, worker, timed_out, time_remaining),
    )


def _verify_one(
    source: ArchiveBucket, destination: ArchiveBucket, entry: ManifestEntry
) -> str | None:
    verified = _verify_archive_copy(source, destination, entry)
    if verified.ok:
        return None
    return f"{entry.key}: {verified.detail}"


def _verify_archive_copy(
    source: ArchiveBucket, destination: ArchiveBucket, entry: ManifestEntry
) -> VerificationResult:
    verified = verify_destination(entry, destination.head_object(entry.key))
    if not verified.ok:
        return verified
    return verify_destination_content(
        source.content_sha256(entry.key, entry.version_id),
        destination.content_sha256(entry.key),
    )


def _cleanup_phase(
    source: ArchiveBucket,
    options: ArchiveOptions,
    entries: tuple[ManifestEntry, ...],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> ArchivePhaseResult:
    if not options.cleanup_enabled:
        return _skipped("cleanup")

    def worker(entry: ManifestEntry) -> str | None:
        return _cleanup_one(source, entry)

    return ArchivePhaseResult(
        "cleanup",
        run_archive_workers(entries, options.max_workers, worker, timed_out, time_remaining),
    )


def _cleanup_one(source: ArchiveBucket, entry: ManifestEntry) -> str | None:
    if entry.version_id is None:
        verified = verify_source_unchanged(entry, source.head_object(entry.key))
        if not verified.ok:
            return f"{entry.key}: {verified.detail}"
    source.delete_source(entry.key, entry.version_id)
    return None


def _skipped(phase: str) -> ArchivePhaseResult:
    return ArchivePhaseResult(phase, skipped=True)


def _timed_out(clock: Callable[[], datetime], deadline: datetime) -> bool:
    return clock() > deadline


def _timeout(phase: str) -> ArchivePhaseResult:
    return ArchivePhaseResult(phase, ("archive run timed out",))


def _empty_manifest(started: datetime, options: ArchiveOptions) -> ArchiveManifest:
    cutoff = started - timedelta(days=options.retention_days)
    return ArchiveManifest(started, cutoff, ())
