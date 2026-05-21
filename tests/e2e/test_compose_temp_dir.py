"""Docker Compose temp-directory bind mount tests."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from s3_archiver_localstack_support.compose import find_repo_root

REPO_ROOT = find_repo_root()


@pytest.mark.e2e()
def test_compose_temp_dir_uses_matching_host_bind_mount(tmp_path: Path) -> None:
    _ = shutil.copy(REPO_ROOT / "compose.yaml", tmp_path / "compose.yaml")
    _ = (tmp_path / ".env").write_text(
        "ARCHIVER_TEMP_DIR=/mnt/data/tmp/s3-archiver\n", encoding="utf-8"
    )
    env = os.environ.copy()
    _ = env.pop("APP_ENV_FILE", None)
    _ = env.pop("ENV_FILE", None)

    result = subprocess.run(
        ["docker", "compose", "config", "app"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "source: /mnt/data/tmp/s3-archiver" in result.stdout
    assert "target: /mnt/data/tmp/s3-archiver" in result.stdout
