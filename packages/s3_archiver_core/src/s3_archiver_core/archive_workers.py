"""Bounded archive worker execution."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait

from s3_archiver_core.archive_manifest import ManifestEntry


def run_archive_workers(
    entries: tuple[ManifestEntry, ...],
    max_workers: int,
    worker: Callable[[ManifestEntry], str | None],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> tuple[str, ...]:
    """Run archive phase workers until all entries complete or timeout expires."""

    failures: list[str] = []
    worker_count = max(1, max_workers)
    executor = ThreadPoolExecutor(max_workers=worker_count)
    try:
        for batch_start in range(0, len(entries), worker_count):
            if timed_out():
                failures.append("archive run timed out")
                break
            batch = entries[batch_start : batch_start + worker_count]
            futures = [executor.submit(_call_worker, worker, entry) for entry in batch]
            done, pending = wait(futures, timeout=time_remaining())
            for future in done:
                failure = _future_result(future)
                if failure is not None:
                    failures.append(failure)
            if pending:
                for future in pending:
                    _ = future.cancel()
                failures.append("archive run timed out")
                break
        return tuple(failures)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


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
