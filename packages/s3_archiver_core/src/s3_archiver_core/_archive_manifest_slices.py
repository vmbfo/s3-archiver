from __future__ import annotations

from collections.abc import Iterable
from itertools import islice
from typing import cast


def slice_items[T](items: Iterable[T], index: slice) -> tuple[T, ...]:
    start = cast(int | None, index.start)
    stop = cast(int | None, index.stop)
    step = cast(int | None, index.step)
    if (
        (step is None or step == 1)
        and (start is None or start >= 0)
        and (stop is None or stop >= 0)
    ):
        return tuple(islice(items, start or 0, stop))
    return tuple(items)[index]
