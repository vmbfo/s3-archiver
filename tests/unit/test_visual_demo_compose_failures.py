"""Failure-path tests for manual visual demo compose orchestration."""

# pyright: reportAny=false, reportPrivateImportUsage=false, reportPrivateLocalImportUsage=false
# pyright: reportUnknownArgumentType=false, reportUnknownLambdaType=false

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest
import s3_archiver_visual_demo.compose as compose_module
from s3_archiver_localstack_support.harness import LocalstackBucketPair

pytestmark = pytest.mark.unit()


def test_seed_run_and_verify_reports_image_build_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    context = _context(tmp_path)
    archive_day = date(2026, 2, 23)

    monkeypatch.setitem(compose_module.__dict__, "_demo_client", lambda *_args: object())
    monkeypatch.setattr(compose_module, "archive_demo_days", lambda _seed_now: (archive_day,))
    monkeypatch.setattr(compose_module, "target_day_demo_cases", lambda *_args: ())
    monkeypatch.setattr(compose_module, "skipped_demo_keys", lambda *_args: ())
    monkeypatch.setattr(compose_module, "expected_archive_members", lambda *_args: {})
    monkeypatch.setattr(compose_module, "expected_direct_destination_keys", lambda *_args: set())
    monkeypatch.setattr(compose_module, "seed_daily_demo_objects", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(compose_module.terminal, "print_image_build_intro", lambda: None)
    monkeypatch.setattr(
        compose_module,
        "run_demo_compose",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ("docker",), 1, stdout="out", stderr="err"
        ),
    )

    with pytest.raises(RuntimeError, match="failed to build the app image"):
        compose_module.__dict__["_seed_run_and_verify"](Path.cwd(), context)


def test_run_archive_reports_failed_archive_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        compose_module,
        "run_demo_compose",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ("docker",), 2, stdout="out", stderr="err"
        ),
    )

    with pytest.raises(RuntimeError, match="archive command failed with exit code 2"):
        compose_module.__dict__["_run_archive"]({}, Path.cwd())


def _context(tmp_path: Path) -> object:
    return compose_module.__dict__["_DemoComposeContext"](
        tmp_path=tmp_path,
        bucket_pair=LocalstackBucketPair("source", "destination"),
        config_json="[]",
        app_env_file=tmp_path / "compose-demo.env",
        compose_env={},
        localstack_endpoint="http://127.0.0.1:4566",
    )
