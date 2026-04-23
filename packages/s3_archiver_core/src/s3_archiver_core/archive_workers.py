"""Bounded archive worker execution."""

from __future__ import annotations

from collections.abc import Callable
from queue import Empty, Queue
from threading import Thread

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
    for batch_start in range(0, len(entries), worker_count):
        if timed_out():
            failures.append("archive run timed out")
            break
        batch = entries[batch_start : batch_start + worker_count]
        results: Queue[str | None] = Queue()
        threads: list[Thread] = []
        for entry in batch:
            thread = Thread(target=_put_worker_result, args=(results, worker, entry))
            thread.start()
            threads.append(thread)
        pending = len(batch)
        try:
            while pending:
                try:
                    failure = results.get(timeout=time_remaining())
                except Empty:
                    failures.append("archive run timed out")
                    return tuple(failures)
                pending -= 1
                if failure is not None:
                    failures.append(failure)
        finally:
            _join_threads(threads)
    return tuple(failures)


def _call_worker(worker: Callable[[ManifestEntry], str | None], entry: ManifestEntry) -> str | None:
    try:
        return worker(entry)
    except Exception as exc:
        return f"{entry.key}: {exc}"


def _put_worker_result(
    results: Queue[str | None],
    worker: Callable[[ManifestEntry], str | None],
    entry: ManifestEntry,
) -> None:
    try:
        failure = _call_worker(worker, entry)
    except Exception as exc:
        failure = f"worker failure: {exc}"
    results.put(failure)


def _join_threads(threads: list[Thread]) -> None:
    for thread in threads:
        thread.join()
