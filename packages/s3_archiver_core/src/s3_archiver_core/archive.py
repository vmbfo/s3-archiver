from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
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
    archive_metadata,
    select_transfer_strategy,
    verify_destination,
    verify_source_unchanged,
)

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

        copy_result = _copy_phase(
            source, destination, options, manifest.entries, debug_logger, timeout, time_remaining
        )
        if _timed_out(now, deadline):
            return ArchiveRunResult(
                run_id, manifest, _timeout("copy"), _skipped("verify"), _skipped("cleanup")
            )
        verify_result = (
            _skipped("verify")
            if not copy_result.ok
            else _verify_phase(destination, options, manifest.entries, timeout, time_remaining)
        )
        if copy_result.ok and _timed_out(now, deadline):
            return ArchiveRunResult(
                run_id, manifest, copy_result, _timeout("verify"), _skipped("cleanup")
            )
        cleanup_result = (
            _cleanup_phase(source, options, manifest.entries, timeout, time_remaining)
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
) -> ArchivePhaseResult:
    def worker(entry: ManifestEntry) -> str | None:
        return _copy_one(source, destination, options, entry, debug_logger)

    return ArchivePhaseResult(
        "copy",
        _run_workers(entries, options.max_workers, worker, timed_out, time_remaining),
    )


def _copy_one(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    options: ArchiveOptions,
    entry: ManifestEntry,
    debug_logger: DebugLogger | None,
) -> str | None:
    existing = destination.head_object(entry.key)
    if existing is not None:
        verified = verify_destination(entry, existing)
        return None if verified.ok else f"{entry.key}: {verified.detail}"
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
    return None


def _verify_phase(
    destination: ArchiveBucket,
    options: ArchiveOptions,
    entries: tuple[ManifestEntry, ...],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> ArchivePhaseResult:
    def worker(entry: ManifestEntry) -> str | None:
        return _verify_one(destination, entry)

    return ArchivePhaseResult(
        "verify",
        _run_workers(entries, options.max_workers, worker, timed_out, time_remaining),
    )


def _verify_one(destination: ArchiveBucket, entry: ManifestEntry) -> str | None:
    verified = verify_destination(entry, destination.head_object(entry.key))
    if verified.ok:
        return None
    return f"{entry.key}: {verified.detail}"


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
        _run_workers(entries, options.max_workers, worker, timed_out, time_remaining),
    )


def _cleanup_one(source: ArchiveBucket, entry: ManifestEntry) -> str | None:
    if entry.version_id is None:
        verified = verify_source_unchanged(entry, source.head_object(entry.key))
        if not verified.ok:
            return f"{entry.key}: {verified.detail}"
    source.delete_source(entry.key, entry.version_id)
    return None


def _run_workers(
    entries: tuple[ManifestEntry, ...],
    max_workers: int,
    worker: Callable[[ManifestEntry], str | None],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> tuple[str, ...]:
    failures: list[str] = []
    worker_count = max(1, max_workers)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for batch_start in range(0, len(entries), worker_count):
            if timed_out():
                failures.append("archive run timed out")
                break
            batch = entries[batch_start : batch_start + worker_count]
            futures: list[Future[str | None]] = [
                executor.submit(_call_worker, worker, entry) for entry in batch
            ]
            done, pending = wait(futures, timeout=time_remaining())
            for future in done:
                failure = _future_result(future)
                if failure is not None:
                    failures.append(failure)
            if pending:
                for future in pending:
                    _ = future.cancel()
                failures.append("archive run timed out")
                executor.shutdown(wait=False, cancel_futures=True)
                break
        return tuple(failures)


def _call_worker(worker: Callable[[ManifestEntry], str | None], entry: ManifestEntry) -> str | None:
    try:
        return worker(entry)
    except Exception as exc:
        return f"{entry.key}: {exc}"


def _future_result(future: Future[str | None]) -> str | None:
    try:
        return future.result()
    except Exception as exc:
        return f"worker failure: {exc}"


def _skipped(phase: str) -> ArchivePhaseResult:
    return ArchivePhaseResult(phase, skipped=True)


def _timed_out(clock: Callable[[], datetime], deadline: datetime) -> bool:
    return clock() > deadline


def _timeout(phase: str) -> ArchivePhaseResult:
    return ArchivePhaseResult(phase, ("archive run timed out",))


def _empty_manifest(started: datetime, options: ArchiveOptions) -> ArchiveManifest:
    cutoff = started - timedelta(days=options.retention_days)
    return ArchiveManifest(started, cutoff, ())
