"""Additional edge-case coverage for cleanup-preview CLI behavior."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import s3_archiver_cli.cleanup_preview as preview_module
from s3_archiver_core.errors import ArchiveRunError
from s3_archiver_core.settings import AppSettings, S3LocationSettings

from tests.unit.archive_workflow_fakes import listed_object as _listed


def _ok_health_check(_settings: AppSettings, _log_file: Path) -> object:
    return object()


def _fake_build_client(_location: S3LocationSettings) -> object:
    return object()


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
        bucket: str

        def __init__(self, _client: object, bucket: str, _temp_dir: Path) -> None:
            self.bucket = bucket

        def versioning_state(self) -> str:
            return "Enabled"

        def list_source_objects(self, _versioning_state: str) -> Iterable[object]:
            return (_listed("archive/old.txt", 61),)

    def fail_write_preview(_payload: dict[str, preview_module.JsonValue], _temp_dir: Path) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(preview_module, "run_health_check", _ok_health_check)
    monkeypatch.setattr(preview_module, "build_s3_client", _fake_build_client)
    monkeypatch.setattr(preview_module, "S3ArchiveBucket", FakeArchiveBucket)
    monkeypatch.setattr(preview_module, "_write_cleanup_preview_file", fail_write_preview)

    with pytest.raises(ArchiveRunError, match="disk full"):
        _ = preview_module.run_cleanup_preview(
            settings,
            settings.log_dir / "s3-archiver.log",
            now=lambda: datetime(2024, 4, 20, tzinfo=UTC),
        )


@pytest.mark.unit()
def test_run_cleanup_preview_uses_default_utc_clock(
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

    class FrozenDateTime:
        @staticmethod
        def now(*, tz: object) -> datetime:
            _ = tz
            return datetime(2024, 4, 20, tzinfo=UTC)

    class FakeArchiveBucket:
        bucket: str

        def __init__(self, _client: object, bucket: str, _temp_dir: Path) -> None:
            self.bucket = bucket

        def versioning_state(self) -> str:
            return "Enabled"

        def list_source_objects(self, _versioning_state: str) -> Iterable[object]:
            return (_listed("archive/old.txt", 61),)

    monkeypatch.setattr(preview_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(preview_module, "run_health_check", _ok_health_check)
    monkeypatch.setattr(preview_module, "build_s3_client", _fake_build_client)
    monkeypatch.setattr(preview_module, "S3ArchiveBucket", FakeArchiveBucket)

    payload = preview_module.run_cleanup_preview(settings, settings.log_dir / "s3-archiver.log")
    preview = cast(dict[str, object], payload["cleanup_preview"])

    assert preview["run_started_at_utc"] == "2024-04-20T00:00:00+00:00"
