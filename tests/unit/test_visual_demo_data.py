"""Unit tests for manual visual demo data helpers."""

# pyright: reportUnknownArgumentType=false, reportUnknownLambdaType=false

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from types import ModuleType
from typing import cast

import pytest
import s3_archiver_visual_demo.data as data_module
from s3_archiver_core.s3 import S3Client
from s3_archiver_localstack_support.harness import LocalstackBucketPair

pytestmark = pytest.mark.unit()


def test_demo_routes_config_and_expected_keys() -> None:
    target_day = date(2026, 2, 23)
    routes = data_module.demo_routes("demo")
    cases = data_module.target_day_demo_cases("demo", target_day)
    archive_days = (target_day,)

    assert [route.name for route in routes] == [
        "direct-daily",
        "direct-copy",
        "filename-daily",
        "filename-copy",
        "folder-daily",
        "folder-copy",
    ]
    assert data_module.demo_config_json(
        LocalstackBucketPair("source", "destination"), prefix="demo"
    ).startswith('[{"copy_mode": "daily_tar_gz"')
    assert len(cases) == 18
    assert len(data_module.expected_archive_members("demo", archive_days)) == 6
    assert len(data_module.expected_direct_destination_keys("demo", archive_days)) == 6
    assert data_module.newer_demo_keys("demo", target_day) == (
        "demo/filename/copy/skips/2026-04-25T00-00-00Z.txt",
        "demo/folder/copy/skips/2026/04/25/future-folder.txt",
    )
    assert data_module.invalid_demo_keys("demo", target_day) == (
        "demo/filename/daily/skips/no-timestamp-latest.txt",
        "demo/folder/daily/skips/no-folder-timestamp.txt",
    )
    assert len(data_module.skipped_demo_keys("demo", target_day)) == (
        data_module.DEMO_SKIPPED_OBJECT_COUNT
    )
    assert data_module.archive_demo_days(datetime(2026, 4, 24, tzinfo=UTC))[0] == target_day
    direct_case = next(case for case in cases if case.route.name == "direct-copy")
    assert direct_case.destination_key == f"mirror/direct/{direct_case.key}"


def test_seed_daily_demo_objects_retries_missing_listings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listed: set[str] = set()
    put_keys: list[str] = []

    def fake_put_object(
        _client: S3Client,
        _bucket: str,
        key: str,
        *,
        metadata: dict[str, str] | None = None,
        tags: dict[str, str] | None = None,
    ) -> dict[str, str]:
        _ = metadata, tags
        put_keys.append(key)
        return {"ETag": "etag"}

    def fake_listed_keys(_client: S3Client, _bucket: str) -> set[str]:
        if not listed:
            listed.update(put_keys)
            return set()
        return set(listed)

    data_time = cast(ModuleType, data_module.__dict__["time"])
    monkeypatch.setattr(data_module, "put_test_object", fake_put_object)
    monkeypatch.setattr(data_module, "listed_keys", fake_listed_keys)
    monkeypatch.setattr(data_time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(data_module, "DEMO_ARCHIVE_DAY_COUNT", 1)

    data_module.seed_daily_demo_objects(
        cast(S3Client, object()),
        "bucket",
        prefix="demo",
        seed_now=datetime(2026, 4, 24, tzinfo=UTC),
    )

    assert len(set(put_keys)) == 22
    assert len(put_keys) == 44


def test_verify_seeded_keys_reports_missing_samples(monkeypatch: pytest.MonkeyPatch) -> None:
    verify_seeded_keys = cast(
        Callable[[S3Client, str, dict[str, dict[str, str]]], None],
        data_module.__dict__["_verify_seeded_keys"],
    )
    data_time = cast(ModuleType, data_module.__dict__["time"])
    monkeypatch.setattr(data_module, "listed_keys", lambda _client, _bucket: set[str]())
    monkeypatch.setattr(data_module, "put_test_object", lambda *_args, **_kwargs: {"ETag": "etag"})
    monkeypatch.setattr(data_time, "sleep", lambda _seconds: None)

    with pytest.raises(AssertionError, match=r"7 seeded demo objects.*\.\.\."):
        verify_seeded_keys(cast(S3Client, object()), "bucket", {f"k{i}": {} for i in range(7)})

    with pytest.raises(AssertionError, match=r"2 seeded demo objects: \['a', 'b'\]"):
        verify_seeded_keys(cast(S3Client, object()), "bucket", {"a": {}, "b": {}})
