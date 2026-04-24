"""Tests for cleanup-preview CLI behavior."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import s3_archiver_cli.cleanup_preview as preview_module
import s3_archiver_cli.main as cli_module
from s3_archiver_core.errors import ArchiveRunError
from s3_archiver_core.settings import AppSettings
from typer.testing import CliRunner

from tests.unit.archive_workflow_fakes import listed_object as _listed

RUNNER = CliRunner()


class _PreviewPayload(dict[str, object]):
    pass


@pytest.mark.unit()
def test_cleanup_preview_command_emits_json_payload(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    monkeypatch.setattr(os, "environ", base_env)

    def configure(_: AppSettings) -> Path:
        return Path("/tmp/s3-archiver.log")

    def run_preview(_settings: AppSettings, _log_file: Path) -> dict[str, object]:
        return {
            "status": "ok",
            "cleanup_preview": {
                "cleanup_enabled_in_settings": False,
                "manifest_file": "/tmp/s3-archiver/cleanup-preview.json",
                "object_count": 1,
                "entries": [{"key": "archive/old.txt"}],
            },
        }

    monkeypatch.setattr(cli_module, "configure_logging", configure)
    monkeypatch.setattr(cli_module, "_run_cleanup_preview", run_preview)

    result = RUNNER.invoke(cli_module.app, ["cleanup-preview"])

    assert result.exit_code == 0
    payload = cast(_PreviewPayload, json.loads(result.stdout))
    assert payload["status"] == "ok"
    assert cast(dict[str, object], payload["cleanup_preview"])["object_count"] == 1


@pytest.mark.unit()
def test_run_cleanup_preview_writes_manifest_file_and_ignores_disabled_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
    tmp_path: Path,
) -> None:
    settings = AppSettings.from_env(base_env)
    settings = AppSettings(
        source=settings.source,
        destination=settings.destination,
        path_filters=settings.path_filters,
        retention_days=60,
        cleanup_enabled=False,
        max_workers=settings.max_workers,
        run_timeout=settings.run_timeout,
        temp_dir=tmp_path / "runtime-temp",
        log_level=settings.log_level,
        log_dir=settings.log_dir,
    )

    class FakeArchiveBucket:
        def __init__(self, _client: object, bucket: str, _temp_dir: Path) -> None:
            self.bucket = bucket

        def versioning_state(self) -> str:
            return "Enabled"

        def list_source_objects(self, _versioning_state: str) -> Iterable[object]:
            return (
                _listed("archive/old.txt", 61),
                _listed("archive/recent.txt", 59),
            )

    monkeypatch.setattr(preview_module, "run_health_check", lambda *_args: object())
    monkeypatch.setattr(preview_module, "build_s3_client", lambda _location: object())
    monkeypatch.setattr(preview_module, "S3ArchiveBucket", FakeArchiveBucket)

    payload = preview_module.run_cleanup_preview(
        settings,
        settings.log_dir / "s3-archiver.log",
        now=lambda: datetime(2024, 4, 20, tzinfo=UTC),
    )

    preview = cast(dict[str, object], payload["cleanup_preview"])
    entries = cast(list[dict[str, object]], preview["entries"])
    manifest_path = Path(cast(str, preview["manifest_file"]))

    assert preview["cleanup_enabled_in_settings"] is False
    assert preview["object_count"] == 1
    assert entries == [
        {
            "etag": '"etag"',
            "key": "archive/old.txt",
            "last_modified_utc": "2024-02-19T00:00:00+00:00",
            "size": 10,
            "source_bucket": settings.source.bucket,
            "version_id": "v1",
        }
    ]
    assert manifest_path.exists()
    written = cast(dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8")))
    written_preview = cast(dict[str, object], written["cleanup_preview"])
    assert written_preview["manifest_file"] == str(manifest_path)


@pytest.mark.unit()
def test_run_cleanup_preview_re_raises_archiver_errors_during_manifest_build(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)
    monkeypatch.setattr(
        preview_module,
        "run_health_check",
        lambda *_args: (_ for _ in ()).throw(ArchiveRunError("health failed")),
    )

    with pytest.raises(ArchiveRunError, match="health failed"):
        _ = preview_module.run_cleanup_preview(settings, settings.log_dir / "s3-archiver.log")


@pytest.mark.unit()
def test_run_cleanup_preview_wraps_unexpected_manifest_build_errors(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
) -> None:
    settings = AppSettings.from_env(base_env)
    monkeypatch.setattr(preview_module, "run_health_check", lambda *_args: object())
    monkeypatch.setattr(
        preview_module,
        "build_s3_client",
        lambda _location: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(ArchiveRunError, match="boom"):
        _ = preview_module.run_cleanup_preview(settings, settings.log_dir / "s3-archiver.log")


@pytest.mark.unit()
def test_run_cleanup_preview_re_raises_archiver_errors_during_manifest_write(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
    tmp_path: Path,
) -> None:
    settings = AppSettings.from_env(base_env)
    settings = AppSettings(
        source=settings.source,
        destination=settings.destination,
        path_filters=settings.path_filters,
        retention_days=60,
        cleanup_enabled=False,
        max_workers=settings.max_workers,
        run_timeout=settings.run_timeout,
        temp_dir=tmp_path / "runtime-temp",
        log_level=settings.log_level,
        log_dir=settings.log_dir,
    )

    class FakeArchiveBucket:
        def __init__(self, _client: object, bucket: str, _temp_dir: Path) -> None:
            self.bucket = bucket

        def versioning_state(self) -> str:
            return "Enabled"

        def list_source_objects(self, _versioning_state: str) -> Iterable[object]:
            return (_listed("archive/old.txt", 61),)

    monkeypatch.setattr(preview_module, "run_health_check", lambda *_args: object())
    monkeypatch.setattr(preview_module, "build_s3_client", lambda _location: object())
    monkeypatch.setattr(preview_module, "S3ArchiveBucket", FakeArchiveBucket)
    monkeypatch.setattr(
        preview_module,
        "_write_cleanup_preview_file",
        lambda *_args: (_ for _ in ()).throw(ArchiveRunError("write failed")),
    )

    with pytest.raises(ArchiveRunError, match="write failed"):
        _ = preview_module.run_cleanup_preview(
            settings,
            settings.log_dir / "s3-archiver.log",
            now=lambda: datetime(2024, 4, 20, tzinfo=UTC),
        )


@pytest.mark.unit()
def test_run_cleanup_preview_wraps_unexpected_manifest_write_errors(
    monkeypatch: pytest.MonkeyPatch,
    base_env: dict[str, str],
    tmp_path: Path,
) -> None:
    settings = AppSettings.from_env(base_env)
    settings = AppSettings(
        source=settings.source,
        destination=settings.destination,
        path_filters=settings.path_filters,
        retention_days=60,
        cleanup_enabled=False,
        max_workers=settings.max_workers,
        run_timeout=settings.run_timeout,
        temp_dir=tmp_path / "runtime-temp",
        log_level=settings.log_level,
        log_dir=settings.log_dir,
    )

    class FakeArchiveBucket:
        def __init__(self, _client: object, bucket: str, _temp_dir: Path) -> None:
            self.bucket = bucket

        def versioning_state(self) -> str:
            return "Enabled"

        def list_source_objects(self, _versioning_state: str) -> Iterable[object]:
            return (_listed("archive/old.txt", 61),)

    monkeypatch.setattr(preview_module, "run_health_check", lambda *_args: object())
    monkeypatch.setattr(preview_module, "build_s3_client", lambda _location: object())
    monkeypatch.setattr(preview_module, "S3ArchiveBucket", FakeArchiveBucket)
    monkeypatch.setattr(
        preview_module,
        "_write_cleanup_preview_file",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("disk full")),
    )

    with pytest.raises(ArchiveRunError, match="disk full"):
        _ = preview_module.run_cleanup_preview(
            settings,
            settings.log_dir / "s3-archiver.log",
            now=lambda: datetime(2024, 4, 20, tzinfo=UTC),
        )


@pytest.mark.unit()
def test_cleanup_preview_utc_now_returns_utc_datetime() -> None:
    assert preview_module._utc_now().tzinfo is UTC
