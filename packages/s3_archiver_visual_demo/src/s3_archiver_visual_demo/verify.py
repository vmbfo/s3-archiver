"""Result verification for the manual visual demo."""

from __future__ import annotations

from datetime import date
from typing import cast

from s3_archiver_core.s3 import S3Client
from s3_archiver_localstack_support.harness import LocalstackBucketPair
from s3_archiver_localstack_support.objects import (
    listed_keys,
    read_object_text,
    read_tar_gz_member_pax_headers,
    read_tar_gz_members_text,
)

from s3_archiver_visual_demo.data import (
    DEMO_ARCHIVE_COUNT,
    DEMO_ARCHIVE_DAY_COUNT,
    DEMO_ARCHIVE_ROOT_COUNT,
    DEMO_DIRECT_COPY_COUNT,
    DEMO_FILES_PER_PATH_DAY,
)
from s3_archiver_visual_demo.expectations import (
    archive_member_name,
    expected_pax_headers,
    sampled_archive_members,
)


def verify_demo_result(
    *,
    output: str,
    payload: dict[str, object],
    destination_client: S3Client,
    bucket_pair: LocalstackBucketPair,
    archive_days: tuple[date, ...],
    archive_members: dict[str, set[str]],
    direct_keys: set[str],
    source_by_destination: dict[str, str],
    source_keys: set[str],
    skipped_count: int,
) -> None:
    """Verify the visual demo output and sampled destination objects."""

    archive_keys = set(archive_members)
    _verify_output(
        output,
        payload,
        archive_days,
        archive_keys,
        direct_keys,
        source_keys,
        skipped_count,
    )
    _verify_archives(
        destination_client,
        bucket_pair,
        archive_members,
        direct_keys,
        source_by_destination,
    )


def _verify_output(
    output: str,
    payload: dict[str, object],
    archive_days: tuple[date, ...],
    archive_keys: set[str],
    direct_keys: set[str],
    source_keys: set[str],
    skipped_count: int,
) -> None:
    required_fragments = (
        "== S3 Archiver Visual Demo ==",
        "== Archive Candidates ==",
        f"archive day count: {DEMO_ARCHIVE_DAY_COUNT}",
        f"archive day range: {min(archive_days)} through {max(archive_days)}",
        f"archive root count: {DEMO_ARCHIVE_ROOT_COUNT}",
        f"archive group count: {DEMO_ARCHIVE_COUNT}",
        f"direct copy count: {DEMO_DIRECT_COPY_COUNT}",
        "source objects per archive: min=2 max=2",
    )
    for fragment in required_fragments:
        if fragment not in output:
            raise RuntimeError(f"visual demo output did not contain {fragment!r}")
    _assert_equal(payload.get("status"), "ok", "payload status")
    archive_manifest = cast(dict[str, object], payload["archive_manifest"])
    archive_result = cast(dict[str, object], payload["archive_result"])
    _assert_equal(
        archive_manifest["object_count"],
        len(source_keys) - skipped_count,
        "manifest object count",
    )
    _assert_equal(
        archive_manifest["destination_archive_keys"],
        sorted(archive_keys),
        "archive keys",
    )
    _assert_equal(
        set(cast(list[str], archive_manifest["destination_keys"])),
        archive_keys | direct_keys,
        "destination keys",
    )
    _assert_equal(archive_manifest["archive_count"], len(archive_keys), "archive count")
    _assert_equal(archive_manifest["direct_copy_count"], len(direct_keys), "direct copy count")
    _assert_equal(
        archive_manifest["skipped_object_count"],
        skipped_count,
        "skipped object count",
    )
    _assert_equal(archive_result["direct_copy_count"], len(direct_keys), "result direct copy count")
    if "cleanup_preview" in payload:
        raise RuntimeError("visual demo unexpectedly emitted cleanup_preview")
    if any("cleanup_status" in group for group in _archive_groups(archive_result)):
        raise RuntimeError("visual demo unexpectedly emitted cleanup_status")
    _assert_equal(_group_source_counts(archive_result), {DEMO_FILES_PER_PATH_DAY}, "group sizes")


def _verify_archives(
    destination_client: S3Client,
    bucket_pair: LocalstackBucketPair,
    archive_members: dict[str, set[str]],
    direct_keys: set[str],
    source_by_destination: dict[str, str],
) -> None:
    _assert_equal(
        listed_keys(destination_client, bucket_pair.destination),
        set(archive_members) | direct_keys,
        "destination object keys",
    )
    for archive_key, members in sampled_archive_members(archive_members).items():
        _assert_equal(
            read_tar_gz_members_text(destination_client, bucket_pair.destination, archive_key),
            {archive_member_name(key): f"payload for {key}\n" for key in members},
            f"archive members for {archive_key}",
        )
        headers = read_tar_gz_member_pax_headers(
            destination_client, bucket_pair.destination, archive_key
        )
        _assert_equal(
            {name: values for name, values in headers.items() if values},
            expected_pax_headers(members),
            f"archive pax headers for {archive_key}",
        )
    for destination_key in _sampled_keys(direct_keys):
        source_key = source_by_destination[destination_key]
        _assert_equal(
            read_object_text(destination_client, bucket_pair.destination, destination_key),
            f"payload for {source_key}\n",
            f"direct object {destination_key}",
        )


def _archive_groups(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], payload["archive_groups"])


def _group_source_counts(payload: dict[str, object]) -> set[int]:
    return {int(cast(int, group["source_object_count"])) for group in _archive_groups(payload)}


def _sampled_keys(keys: set[str]) -> tuple[str, str, str]:
    sorted_keys = sorted(keys)
    return sorted_keys[0], sorted_keys[len(sorted_keys) // 2], sorted_keys[-1]


def _assert_equal(left: object, right: object, label: str) -> None:
    if left != right:
        raise RuntimeError(f"unexpected {label}: {left!r} != {right!r}")
