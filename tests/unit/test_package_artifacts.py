"""Unit tests for explicit package artifact exclusions."""

from __future__ import annotations

import subprocess
import tarfile
import tomllib
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_CONFIGS = {
    "s3-archiver-core": REPO_ROOT / "packages" / "s3_archiver_core" / "pyproject.toml",
    "s3-archiver-cli": REPO_ROOT / "packages" / "s3_archiver_cli" / "pyproject.toml",
}
REQUIRED_EXCLUDES = {"/tests", "/docker", "localstack", "test-support"}


def _as_mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    mapping = cast(dict[object, object], value)
    return {str(key): entry for key, entry in mapping.items()}


def _as_string_list(value: object) -> list[str]:
    assert isinstance(value, list)
    entries = cast(list[object], value)
    return [str(entry) for entry in entries]


def _build_backend_config(pyproject_path: Path) -> dict[str, list[str]]:
    config = _as_mapping(tomllib.loads(pyproject_path.read_text(encoding="utf-8")))
    tool = _as_mapping(config["tool"])
    uv = _as_mapping(tool["uv"])
    build_backend = _as_mapping(uv["build-backend"])
    return {
        "source-exclude": _as_string_list(build_backend["source-exclude"]),
        "wheel-exclude": _as_string_list(build_backend["wheel-exclude"]),
    }


@pytest.mark.unit()
def test_package_build_configs_explicitly_exclude_test_and_localstack_assets() -> None:
    for pyproject_path in PACKAGE_CONFIGS.values():
        build_backend = _build_backend_config(pyproject_path)

        assert REQUIRED_EXCLUDES.issubset(build_backend["source-exclude"])
        assert REQUIRED_EXCLUDES.issubset(build_backend["wheel-exclude"])


@pytest.mark.unit()
def test_built_distributions_exclude_test_and_localstack_assets(tmp_path: Path) -> None:
    for package_name in PACKAGE_CONFIGS:
        output_dir = tmp_path / package_name
        _ = subprocess.run(
            [
                "uv",
                "build",
                "--package",
                package_name,
                "--out-dir",
                str(output_dir),
                "--sdist",
                "--wheel",
                "--no-build-logs",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        member_names = {
            member_name
            for artifact_path in output_dir.iterdir()
            if artifact_path.suffix == ".whl" or artifact_path.suffixes[-2:] == [".tar", ".gz"]
            for member_name in _distribution_members(artifact_path)
        }

        assert member_names
        assert not any(_is_forbidden_member(member_name) for member_name in member_names)


def _distribution_members(artifact_path: Path) -> Iterable[str]:
    if artifact_path.suffix == ".whl":
        with zipfile.ZipFile(artifact_path) as wheel:
            yield from wheel.namelist()
        return
    if artifact_path.suffixes[-2:] == [".tar", ".gz"]:
        with tarfile.open(artifact_path, "r:gz") as sdist:
            for member in sdist.getmembers():
                yield member.name
        return
    raise AssertionError(f"Unexpected build artifact {artifact_path}")


def _is_forbidden_member(member_name: str) -> bool:
    normalized = member_name.strip("/")
    parts = normalized.split("/")
    return any(part in {"tests", "docker", "localstack", "test-support"} for part in parts)
