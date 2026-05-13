"""Command-line entry point for the manual visual demo."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from s3_archiver_visual_demo.compose import run


def main(argv: Sequence[str] | None = None) -> None:
    """Run the manual visual demo CLI."""

    try:
        run(keep_compose=_parse_keep_compose(sys.argv[1:] if argv is None else argv))
    except Exception as exc:
        print(f"s3-archiver visual demo failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def _parse_keep_compose(argv: Sequence[str]) -> bool:
    if not argv:
        return False
    if tuple(argv) == ("--keep-compose",):
        return True
    if tuple(argv) in {("-h",), ("--help",)}:
        print("usage: s3-archiver-visual-demo [--keep-compose]")
        raise SystemExit(0)
    raise RuntimeError(f"Unsupported arguments: {' '.join(argv)}")
