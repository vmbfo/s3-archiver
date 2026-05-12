"""Unit tests for manual visual demo terminal rendering."""

# pyright: reportAny=false, reportPrivateLocalImportUsage=false

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
import s3_archiver_visual_demo.terminal as terminal_module

pytestmark = pytest.mark.unit()


def test_terminal_section_helpers(capsys: pytest.CaptureFixture[str]) -> None:
    terminal_module.print_image_build_intro()
    terminal_module.print_demo_intro(seeded_count=10)

    output = capsys.readouterr().out
    assert "PREPARING THE RUNTIME IMAGE" in output
    assert "RUNNING THE COMPOSE-BACKED DEMO" in output
    assert "Seeded 10 source objects" in output
    assert terminal_module.build_failure_message("out", "err") == (
        "failed to build the app image\nstdout:\nout\nstderr:\nerr"
    )


def test_visual_demo_printer_streams_and_filters_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    printer = terminal_module.VisualDemoPrinter(60)
    for line in (
        "== S3 Archiver Visual Demo ==",
        "== Preflight ==",
        "== Before archive ==",
        "== Archive Candidates ==",
        "Running archive workflow against the configured buckets...",
        "== Archive Result ==",
        "== After archive ==",
        '{"status":"ok"}',
        "Demo summary JSON follows on the next line.",
        "Container app exited",
        "Volume data removed",
        "",
        "SOURCE key=a size=1 last_modified=2026-01-01T00:00:00+00:00 eligible=True",
        "DEST   key=b size=2 last_modified=2026-01-01T00:00:00+00:00 "
        + "present_in_destination=False",
        "COPY   key=c size=3 last_modified=2026-01-01T00:00:00+00:00 "
        + "version_id=v1 source_last_modified=then",
        "DELETE key=d size=4 last_modified=2026-01-01T00:00:00+00:00",
        "GROUP  route=demo",
        "DIRECT route=demo",
        "SOURCE key=e size=5 last_modified=2026-01-01T00:00:00+00:00",
        "plain text",
    ):
        printer.emit(line)
    printer.finish()

    output = capsys.readouterr().out
    assert "S3 ARCHIVER VISUAL DEMO" in output
    assert "source | 2026-01-01 00:00:00" in output
    assert "plain text" in output
    assert '{"status":"ok"}' not in output


def test_friendly_line_helpers_cover_fallbacks() -> None:
    friendly = cast(Callable[[str], str], terminal_module.__dict__["_friendly_demo_line"])
    field = cast(Callable[[str, str], str | None], terminal_module.__dict__["_field"])
    printer = terminal_module.__dict__["_SampledDemoPrinter"](60)

    assert friendly("SOURCE missing-fields") == "source | missing-fields"
    assert friendly("unchanged") == "unchanged"
    assert field("a=1 b=2", "missing") is None
    for key in ("a", "b", "c", "d"):
        printer.print_line(f"SOURCE key={key} size=1 last_modified=2026-01-01T00:00:00+00:00")
    printer.finish()
