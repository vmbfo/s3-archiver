"""Tests for archive runtime temp-file handling."""

from __future__ import annotations

from pathlib import Path

import pytest
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.temp_files import (
    TRANSFER_TEMP_PREFIX,
    cleanup_stale_transfer_files,
    prepare_runtime_temp_dir,
)


@pytest.mark.unit()
def test_prepare_runtime_temp_dir_removes_stale_archiver_files(tmp_path: Path) -> None:
    temp_dir = tmp_path / "runtime-temp"
    temp_dir.mkdir()
    stale = temp_dir / f"{TRANSFER_TEMP_PREFIX}old"
    unrelated = temp_dir / "application-owned"
    _ = stale.write_bytes(b"old")
    _ = unrelated.write_bytes(b"keep")

    prepare_runtime_temp_dir(temp_dir)

    assert not stale.exists()
    assert unrelated.read_bytes() == b"keep"


@pytest.mark.unit()
def test_prepare_runtime_temp_dir_rejects_file_path(tmp_path: Path) -> None:
    temp_dir = tmp_path / "not-a-dir"
    _ = temp_dir.write_bytes(b"")

    with pytest.raises(ConfigError, match="ARCHIVER_TEMP_DIR"):
        prepare_runtime_temp_dir(temp_dir)


@pytest.mark.unit()
def test_prepare_runtime_temp_dir_rejects_path_that_is_not_directory_after_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "runtime-temp"

    def noop_mkdir(self: Path, parents: bool = False, exist_ok: bool = False) -> None:
        _ = (self, parents, exist_ok)

    def never_directory(self: Path) -> bool:
        _ = self
        return False

    monkeypatch.setattr(Path, "mkdir", noop_mkdir)
    monkeypatch.setattr(Path, "is_dir", never_directory)

    with pytest.raises(ConfigError, match="ARCHIVER_TEMP_DIR must be a directory"):
        prepare_runtime_temp_dir(temp_dir)


@pytest.mark.unit()
def test_prepare_runtime_temp_dir_wraps_creation_failures(tmp_path: Path) -> None:
    temp_parent = tmp_path / "not-a-dir"
    _ = temp_parent.write_bytes(b"")

    with pytest.raises(ConfigError, match="ARCHIVER_TEMP_DIR cannot be created"):
        prepare_runtime_temp_dir(temp_parent / "runtime")


@pytest.mark.unit()
def test_cleanup_stale_transfer_files_keeps_directories(tmp_path: Path) -> None:
    temp_dir = tmp_path / "runtime-temp"
    temp_dir.mkdir()
    transfer_dir = temp_dir / f"{TRANSFER_TEMP_PREFIX}directory"
    transfer_dir.mkdir()

    cleanup_stale_transfer_files(temp_dir)

    assert transfer_dir.is_dir()
