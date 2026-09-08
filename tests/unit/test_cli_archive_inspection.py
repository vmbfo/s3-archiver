"""Exercise the public archive tools command wiring."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import s3_archiver_cli.archive_inspection_commands as commands
from s3_archiver_cli.main import app
from s3_archiver_core.archive import ArchiveRoute
from s3_archiver_core.archive_routes import BuildS3Client
from s3_archiver_core.settings import AppSettings
from typer.testing import CliRunner

from tests.unit.archive_workflow_fakes import FakeBucket, archive_routes
from tests.unit.test_archive_refresh_safety import KEY, objects, run


@pytest.mark.unit()
@pytest.mark.parametrize("action", ["list", "verify", "extract", "unknown-route", "missing-key"])
def test_archive_tools_cli(
    tmp_path: Path, base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    source = FakeBucket("source", objects(0), temp_dir=tmp_path)
    destination = FakeBucket("destination", temp_dir=tmp_path)
    result = run(source, destination)
    assert result.ok
    monkeypatch.setattr(os, "environ", base_env)

    def routes(_settings: AppSettings, _build: BuildS3Client) -> tuple[ArchiveRoute, ...]:
        return archive_routes(source, destination)

    monkeypatch.setattr(commands, "archive_routes_from_settings", routes)
    key = "missing" if action == "missing-key" else KEY
    route = "missing" if action == "unknown-route" else "default"
    subcommand = "verify" if action in {"unknown-route", "missing-key"} else action
    args = ["archive-tools", subcommand, route, key]
    if action == "extract":
        args.extend([str(tmp_path / "restored"), "--member", objects(0)[0].key])
    invoked = CliRunner().invoke(app, args)
    assert invoked.exit_code == (1 if action in {"unknown-route", "missing-key"} else 0)
    if action == "extract":
        assert (tmp_path / "restored" / objects(0)[0].key).is_file()
    result.close()
