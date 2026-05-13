from __future__ import annotations

from collections.abc import Callable
from queue import Empty, Queue
from threading import Thread


def run_parallel_items[T](
    items: tuple[T, ...],
    worker: Callable[[T], tuple[str, ...]],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> tuple[str, ...]:
    """Run one worker per item and collect failure messages."""

    if not items:
        return ()
    if timed_out():
        return ("archive run timed out",)
    results: Queue[tuple[str, ...]] = Queue()
    for item in items:
        thread = Thread(target=_put_worker_result, args=(results, worker, item), daemon=True)
        thread.start()
    failures: list[str] = []
    pending = len(items)
    while pending:
        try:
            route_failures = results.get(timeout=time_remaining())
        except Empty:
            failures.append("archive run timed out")
            return tuple(failures)
        pending -= 1
        failures.extend(route_failures)
    return tuple(failures)


def _put_worker_result[T](
    results: Queue[tuple[str, ...]],
    worker: Callable[[T], tuple[str, ...]],
    item: T,
) -> None:
    try:
        failures = worker(item)
    except Exception as exc:
        failures = (f"{item}: {exc}",)
    results.put(failures)
