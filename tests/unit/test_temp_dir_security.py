"""Security tests for the runtime temporary directory."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.temp_files import ensure_runtime_temp_dir


@pytest.mark.unit()
def test_ensure_runtime_temp_dir_creates_owner_only_directory(tmp_path: Path) -> None:
    temp_dir = tmp_path / "runtime-temp"

    ensure_runtime_temp_dir(temp_dir)

    metadata = temp_dir.stat()
    assert metadata.st_uid == os.geteuid()
    assert stat.S_IMODE(metadata.st_mode) == 0o700


@pytest.mark.unit()
def test_ensure_runtime_temp_dir_repairs_permissions(tmp_path: Path) -> None:
    temp_dir = tmp_path / "runtime-temp"
    temp_dir.mkdir(mode=0o755)
    temp_dir.chmod(0o755)

    ensure_runtime_temp_dir(temp_dir)

    assert stat.S_IMODE(temp_dir.stat().st_mode) == 0o700


@pytest.mark.unit()
def test_ensure_runtime_temp_dir_rejects_directory_owned_by_another_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "runtime-temp"
    temp_dir.mkdir()
    actual_uid = temp_dir.stat().st_uid
    monkeypatch.setattr("s3_archiver_core.temp_files.os.geteuid", lambda: actual_uid + 1)

    with pytest.raises(ConfigError, match="must be owned by the current user"):
        ensure_runtime_temp_dir(temp_dir)


@pytest.mark.unit()
def test_ensure_runtime_temp_dir_rejects_symbolic_link(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    temp_dir = tmp_path / "runtime-temp"
    temp_dir.symlink_to(target, target_is_directory=True)

    with pytest.raises(ConfigError, match="must not be a symbolic link"):
        ensure_runtime_temp_dir(temp_dir)


@pytest.mark.unit()
def test_ensure_runtime_temp_dir_wraps_inspection_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "runtime-temp"
    temp_dir.mkdir()

    def fail_stat(self: Path) -> os.stat_result:
        _ = self
        raise PermissionError(13, "Permission denied", str(temp_dir))

    def is_directory(_path: Path) -> bool:
        return True

    def is_not_symlink(_path: Path) -> bool:
        return False

    monkeypatch.setattr(Path, "is_dir", is_directory)
    monkeypatch.setattr(Path, "is_symlink", is_not_symlink)
    monkeypatch.setattr(Path, "stat", fail_stat)

    with pytest.raises(ConfigError, match="cannot be inspected"):
        ensure_runtime_temp_dir(temp_dir)


@pytest.mark.unit()
def test_ensure_runtime_temp_dir_wraps_permission_hardening_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "runtime-temp"
    temp_dir.mkdir()

    def fail_chmod(self: Path, mode: int) -> None:
        _ = (self, mode)
        raise PermissionError(13, "Permission denied", str(temp_dir))

    monkeypatch.setattr(Path, "chmod", fail_chmod)

    with pytest.raises(ConfigError, match="cannot be secured"):
        ensure_runtime_temp_dir(temp_dir)


@pytest.mark.unit()
def test_ensure_runtime_temp_dir_rejects_permissions_that_cannot_be_hardened(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temp_dir = tmp_path / "runtime-temp"
    temp_dir.mkdir()
    temp_dir.chmod(0o755)

    def noop_chmod(_path: Path, _mode: int) -> None:
        return

    monkeypatch.setattr(Path, "chmod", noop_chmod)

    with pytest.raises(ConfigError, match="must have owner-only permissions"):
        ensure_runtime_temp_dir(temp_dir)
