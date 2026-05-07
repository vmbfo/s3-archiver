"""Tests for the visual demo CLI command workflow."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
import s3_archiver_cli.main as cli_module
import s3_archiver_cli.visual_demo as demo_module
import s3_archiver_cli.visual_demo_command as demo_command_module
import typer
from s3_archiver_core.settings import AppSettings
from typer.testing import CliRunner

RUNNER = CliRunner()


def _configure_logging(_: AppSettings) -> Path:
    return Path("/tmp/s3-archiver.log")


@pytest.mark.unit()
def test_demo_command_relays_visual_output_and_summary_json(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)

    def run_command(
        *,
        run_payload_command: object,
        archive_runner: object,
        emit: Callable[[str], None],
    ) -> None:
        _ = run_payload_command, archive_runner
        emit("== S3 Archiver Visual Demo ==")
        payload: dict[str, demo_module.JsonValue] = {"status": "ok"}
        emit(json.dumps(payload, sort_keys=True))

    monkeypatch.setattr(cli_module, "configure_logging", _configure_logging)
    monkeypatch.setattr(demo_command_module, "run", run_command)

    result = RUNNER.invoke(cli_module.app, ["demo"])

    assert result.exit_code == 0
    assert "== S3 Archiver Visual Demo ==" in result.stdout
    payload = cast(dict[str, object], json.loads(result.stdout.splitlines()[-1]))
    assert payload["status"] == "ok"


@pytest.mark.unit()
def test_demo_command_exits_non_zero_when_summary_reports_error(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)
    monkeypatch.setattr(cli_module, "configure_logging", _configure_logging)

    def run_command(
        *,
        run_payload_command: object,
        archive_runner: object,
        emit: Callable[[str], None],
    ) -> None:
        _ = run_payload_command, archive_runner, emit
        emit(json.dumps({"status": "error"}, sort_keys=True))
        raise typer.Exit(code=1)

    monkeypatch.setattr(demo_command_module, "run", run_command)

    result = RUNNER.invoke(cli_module.app, ["demo"])

    assert result.exit_code == 1


@pytest.mark.unit()
def test_visual_demo_command_run_allows_ok_summary() -> None:
    captured: list[object] = []

    def run_payload_command(
        command: Callable[[AppSettings, Path], dict[str, demo_module.JsonValue]],
    ) -> dict[str, demo_module.JsonValue]:
        captured.append(command)
        return {"status": "ok"}

    demo_command_module.run(
        run_payload_command=run_payload_command,
        archive_runner=lambda _settings, _log_file: {"status": "ok"},
        emit=lambda _line: None,
    )

    assert len(captured) == 1


@pytest.mark.unit()
def test_visual_demo_command_run_exits_for_error_summary() -> None:
    def run_payload_command(
        command: Callable[[AppSettings, Path], dict[str, demo_module.JsonValue]],
    ) -> dict[str, demo_module.JsonValue]:
        _ = command
        return {"status": "error"}

    with pytest.raises(typer.Exit) as exc_info:
        demo_command_module.run(
            run_payload_command=run_payload_command,
            archive_runner=lambda _settings, _log_file: {"status": "ok"},
            emit=lambda _line: None,
        )

    assert exc_info.value.exit_code == 1
