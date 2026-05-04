"""CLI wrapper for visual demo commands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer
from s3_archiver_core.settings import AppSettings

from s3_archiver_cli.visual_demo import (
    ArchiveRunner,
    Emitter,
    JsonValue,
    run_visual_demo,
)

type PayloadCommand = Callable[
    [Callable[[AppSettings, Path], dict[str, JsonValue]]], dict[str, JsonValue]
]


def run(
    *,
    run_payload_command: PayloadCommand,
    archive_runner: ArchiveRunner,
    emit: Emitter,
) -> None:
    """Run a visual demo command and map unsuccessful summaries to CLI failure."""

    payload = run_payload_command(
        lambda settings, log_file: run_visual_demo(
            settings,
            log_file,
            archive_runner=archive_runner,
            emit=emit,
        )
    )
    if payload.get("status") != "ok":
        raise typer.Exit(code=1)
