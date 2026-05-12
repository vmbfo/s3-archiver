"""Production-shaped source data for the manual visual demo."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from s3_archiver_core.s3 import S3Client
from s3_archiver_localstack_support.harness import LocalstackBucketPair
from s3_archiver_localstack_support.objects import listed_keys, put_test_object

DEMO_ARCHIVE_START_AGE_DAYS = 60
DEMO_ARCHIVE_DAY_COUNT = 365
DEMO_FILES_PER_PATH_DAY = 2
DEMO_SKIPPED_OBJECT_COUNT = 4
DEMO_ARCHIVE_ROOT_COUNT = 6
DEMO_ARCHIVE_COUNT = DEMO_ARCHIVE_DAY_COUNT * DEMO_ARCHIVE_ROOT_COUNT
DEMO_DIRECT_COPY_COUNT = DEMO_ARCHIVE_DAY_COUNT * 3 * DEMO_FILES_PER_PATH_DAY
DEMO_ARCHIVED_OBJECT_COUNT = DEMO_ARCHIVE_COUNT * DEMO_FILES_PER_PATH_DAY
DEMO_DIRECT_OBJECT_COUNT = DEMO_DIRECT_COPY_COUNT
DEMO_ELIGIBLE_OBJECT_COUNT = DEMO_ARCHIVED_OBJECT_COUNT + DEMO_DIRECT_OBJECT_COUNT
DEMO_SEEDED_OBJECT_COUNT = DEMO_ELIGIBLE_OBJECT_COUNT + DEMO_SKIPPED_OBJECT_COUNT


@dataclass(frozen=True, slots=True)
class DemoRoute:
    """Visual-demo route configuration."""

    name: str
    parser: str
    copy_mode: str
    source_path: str
    destination_path: str


@dataclass(frozen=True, slots=True)
class DemoObjectCase:
    """Expected source and destination keys for one demo object."""

    route: DemoRoute
    key: str
    destination_key: str


def demo_routes(prefix: str) -> tuple[DemoRoute, ...]:
    """Return the route set used by the manual visual demo."""

    filename = "filename_timestamp"
    folder = "folder_timestamp"
    daily = "daily_tar_gz"
    return (
        DemoRoute("direct-daily", "direct", daily, f"{prefix}/direct/daily", "archives/direct"),
        DemoRoute("direct-copy", "direct", "direct", f"{prefix}/direct/copy", "mirror/direct"),
        DemoRoute(
            "filename-daily", filename, daily, f"{prefix}/filename/daily", "archives/filename"
        ),
        DemoRoute(
            "filename-copy", filename, "direct", f"{prefix}/filename/copy", "mirror/filename"
        ),
        DemoRoute("folder-daily", folder, daily, f"{prefix}/folder/daily", "archives/folder"),
        DemoRoute("folder-copy", folder, "direct", f"{prefix}/folder/copy", "mirror/folder"),
    )


def demo_config_json(bucket_pair: LocalstackBucketPair, *, prefix: str) -> str:
    """Render the visual-demo route configuration as app JSON."""

    routes = [
        {
            "name": route.name,
            "parser": route.parser,
            "copy_mode": route.copy_mode,
            "source": {"bucket": bucket_pair.source, "path": route.source_path},
            "destination": {"bucket": bucket_pair.destination, "path": route.destination_path},
        }
        for route in demo_routes(prefix)
    ]
    return json.dumps(routes, sort_keys=True)


def seed_daily_demo_objects(
    client: S3Client,
    bucket: str,
    *,
    prefix: str,
    seed_now: datetime,
) -> None:
    """Seed source objects used by the visual demo archive run."""

    seeded_now = seed_now.astimezone(UTC).replace(microsecond=0)
    target_day = archive_demo_days(seeded_now)[0]
    metadata_by_key: dict[str, dict[str, str]] = {}
    keys_by_age = _archive_demo_keys_by_age(prefix, seeded_now) | {
        DEMO_ARCHIVE_START_AGE_DAYS - 1: newer_demo_keys(prefix, target_day),
        0: invalid_demo_keys(prefix, target_day),
    }
    for age_days, keys in keys_by_age.items():
        target = seeded_now - timedelta(days=age_days)
        for key in keys:
            metadata = {
                "s3-archiver-test-age-days": str(age_days),
                "s3-archiver-test-last-modified": target.isoformat(),
            }
            metadata_by_key[key] = metadata
            _ = put_test_object(client, bucket, key, metadata=metadata)
    _verify_seeded_keys(client, bucket, metadata_by_key)


def archive_demo_days(seed_now: datetime) -> tuple[date, ...]:
    """Return the days expected to be archived by the visual demo."""

    target_day = seed_now.astimezone(UTC).date() - timedelta(days=DEMO_ARCHIVE_START_AGE_DAYS)
    return tuple(target_day - timedelta(days=offset) for offset in range(DEMO_ARCHIVE_DAY_COUNT))


def target_day_demo_cases(prefix: str, target_day: date) -> tuple[DemoObjectCase, ...]:
    """Return source and destination cases for one demo archive day."""

    routes = {route.name: route for route in demo_routes(prefix)}
    return (
        *_direct_daily_cases(routes["direct-daily"], target_day),
        *_direct_cases(routes["direct-copy"], target_day),
        *_timestamp_cases(prefix, routes, target_day),
    )


def newer_demo_keys(prefix: str, target_day: date) -> tuple[str, str]:
    """Return demo keys that should be skipped because they are too new."""

    future_day = target_day + timedelta(days=61)
    return (
        f"{prefix}/filename/copy/skips/{future_day.isoformat()}T00-00-00Z.txt",
        f"{prefix}/folder/copy/skips/{future_day:%Y/%m/%d}/future-folder.txt",
    )


def invalid_demo_keys(prefix: str, target_day: date) -> tuple[str, str]:
    """Return demo keys that should be skipped because timestamps are invalid."""

    _ = target_day
    return (
        f"{prefix}/filename/daily/skips/no-timestamp-latest.txt",
        f"{prefix}/folder/daily/skips/no-folder-timestamp.txt",
    )


def expected_archive_members(prefix: str, archive_days: tuple[date, ...]) -> dict[str, set[str]]:
    """Return expected tar archive members keyed by destination archive key."""

    members: dict[str, set[str]] = {}
    for target_day in archive_days:
        for case in target_day_demo_cases(prefix, target_day):
            if case.route.copy_mode == "daily_tar_gz":
                members.setdefault(case.destination_key, set()).add(case.key)
    return members


def expected_direct_destination_keys(prefix: str, archive_days: tuple[date, ...]) -> set[str]:
    """Return expected direct-copy destination keys for the demo run."""

    return {
        case.destination_key
        for target_day in archive_days
        for case in target_day_demo_cases(prefix, target_day)
        if case.route.copy_mode == "direct"
    }


def _direct_cases(route: DemoRoute, target_day: date) -> tuple[DemoObjectCase, DemoObjectCase]:
    day = target_day.isoformat()
    return (
        _case(route, f"{route.source_path}/station-a/{day}-last-modified-a.txt", target_day),
        _case(route, f"{route.source_path}/station-b/{day}-last-modified-b.txt", target_day),
    )


def _direct_daily_cases(route: DemoRoute, target_day: date) -> tuple[DemoObjectCase, ...]:
    day = target_day.isoformat()
    return tuple(
        _case(
            route,
            f"{route.source_path}/{station}/{day}-last-modified-{suffix}.txt",
            target_day,
        )
        for station in ("station-a", "station-b")
        for suffix in ("a", "b")
    )


def _timestamp_cases(
    prefix: str, routes: dict[str, DemoRoute], target_day: date
) -> tuple[DemoObjectCase, ...]:
    day = target_day.isoformat()
    path_day = target_day.strftime("%Y/%m/%d")
    specs = (
        (
            routes["filename-daily"],
            f"{prefix}/filename/daily/fae",
            (
                f"{prefix}/filename/daily/fae/{day}T07-00-00Z.xml",
                f"{prefix}/filename/daily/fae/{day}T08-15-00Z.xml",
            ),
        ),
        (
            routes["filename-daily"],
            f"{prefix}/filename/daily/harmonie",
            (
                f"{prefix}/filename/daily/harmonie/HARMONIE_{day}T060000Z.bz2",
                f"{prefix}/filename/daily/harmonie/HARMONIE_{day}T120000Z.bz2",
            ),
        ),
        (
            routes["filename-copy"],
            f"{prefix}/filename/copy/metar",
            (f"{prefix}/filename/copy/metar/METAR_{target_day:%Y%m%d}120000Z.json",),
        ),
        (
            routes["filename-copy"],
            f"{prefix}/filename/copy/radar",
            (f"{prefix}/filename/copy/radar/radar_{target_day:%Y%m%d}-130000.bin",),
        ),
        (
            routes["folder-daily"],
            f"{prefix}/folder/daily/satellite",
            (
                f"{prefix}/folder/daily/satellite/{path_day}/14/sat-latest.png",
                f"{prefix}/folder/daily/satellite/{path_day}/14/sat-hourly.png",
            ),
        ),
        (
            routes["folder-daily"],
            f"{prefix}/folder/daily/models",
            (
                f"{prefix}/folder/daily/models/{path_day}/16/model.grib",
                f"{prefix}/folder/daily/models/{path_day}/16/model-boundary.grib",
            ),
        ),
        (
            routes["folder-copy"],
            f"{prefix}/folder/copy/ocean",
            (f"{prefix}/folder/copy/ocean/{path_day}/18/wave.nc",),
        ),
        (
            routes["folder-copy"],
            f"{prefix}/folder/copy/climate",
            (f"{prefix}/folder/copy/climate/{path_day}/20/summary.txt",),
        ),
    )
    return tuple(
        DemoObjectCase(route, key, _destination_key(route, key, root, target_day))
        for route, root, keys in specs
        for key in keys
    )


def _case(route: DemoRoute, key: str, target_day: date) -> DemoObjectCase:
    root = key.rsplit("/", maxsplit=1)[0]
    return DemoObjectCase(route, key, _destination_key(route, key, root, target_day))


def _destination_key(route: DemoRoute, key: str, archive_root: str, target_day: date) -> str:
    if route.copy_mode == "direct":
        return f"{route.destination_path}/{key}"
    relative_root = archive_root.removeprefix(f"{route.source_path}/")
    return f"{route.destination_path}/{relative_root}/{target_day.isoformat()}.tar.gz"


def _verify_seeded_keys(
    client: S3Client, bucket: str, metadata_by_key: dict[str, dict[str, str]]
) -> None:
    expected = set(metadata_by_key)
    for _ in range(30):
        missing = expected - listed_keys(client, bucket)
        if not missing:
            return
        for key in sorted(missing):
            _ = put_test_object(client, bucket, key, metadata=metadata_by_key[key])
        time.sleep(1.0)
    missing = sorted(expected - listed_keys(client, bucket))
    sample = [*missing[:3], "...", *missing[-3:]] if len(missing) > 6 else missing
    raise AssertionError(f"LocalStack did not list {len(missing)} seeded demo objects: {sample}")


def _archive_demo_keys_by_age(prefix: str, seed_now: datetime) -> dict[int, tuple[str, ...]]:
    target_day = seed_now.astimezone(UTC).date() - timedelta(days=DEMO_ARCHIVE_START_AGE_DAYS)
    return {
        DEMO_ARCHIVE_START_AGE_DAYS + offset: tuple(
            case.key for case in target_day_demo_cases(prefix, target_day - timedelta(days=offset))
        )
        for offset in range(DEMO_ARCHIVE_DAY_COUNT)
    }
