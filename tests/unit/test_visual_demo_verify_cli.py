"""Unit tests for manual visual demo verification and CLI helpers."""

# pyright: reportUnknownArgumentType=false, reportUnknownLambdaType=false
# pyright: reportUnknownVariableType=false

from __future__ import annotations

from datetime import date
from typing import cast

import pytest
import s3_archiver_visual_demo.cli as cli_module
import s3_archiver_visual_demo.expectations as expectations_module
import s3_archiver_visual_demo.summary as summary_module
import s3_archiver_visual_demo.verify as verify_module
from s3_archiver_core.archive_tar import ORIGINAL_KEY_PAX_HEADER
from s3_archiver_core.s3 import S3Client
from s3_archiver_localstack_support.harness import LocalstackBucketPair

pytestmark = pytest.mark.unit()


def test_expectation_helpers_sample_archives_and_pax_headers() -> None:
    archive_members = {"a": {"plain"}, "b": {"C:/unsafe"}, "c": {"s3-archiver-safe/key"}}
    sampled = expectations_module.sampled_archive_members(archive_members)

    assert sampled == archive_members
    assert expectations_module.archive_member_name("plain") == "plain"
    headers = expectations_module.expected_pax_headers({"plain", "C:/unsafe"})
    assert list(headers.values()) == [{ORIGINAL_KEY_PAX_HEADER: "C:/unsafe"}]


def test_print_verified_summary(capsys: pytest.CaptureFixture[str]) -> None:
    summary_module.print_verified_summary(
        {"status": "ok"},
        total_count=3,
        copied_count=2,
        remaining_source_count=1,
    )

    output = capsys.readouterr().out
    assert "VERIFIED RESULT" in output
    assert "source objects seeded: 3" in output


def test_cli_main_parses_arguments_and_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(cli_module, "run", lambda *, keep_compose: calls.append(keep_compose))

    cli_module.main([])
    cli_module.main(["--keep-compose"])
    assert calls == [False, True]

    with pytest.raises(SystemExit) as help_exit:
        cli_module.main(["--help"])
    assert help_exit.value.code == 0

    with pytest.raises(SystemExit) as bad_arg_exit:
        cli_module.main(["--bad"])
    assert bad_arg_exit.value.code == 1

    monkeypatch.setattr(
        cli_module,
        "run",
        lambda *, keep_compose: (_ for _ in ()).throw(RuntimeError("failed")),
    )
    with pytest.raises(SystemExit) as run_exit:
        cli_module.main([])
    assert run_exit.value.code == 1


def test_verify_demo_result_accepts_expected_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    archive_days, archive_members, direct_keys, source_by_destination, source_keys = (
        _verify_inputs()
    )
    payload = _payload(archive_days, archive_members, direct_keys, source_keys)
    pair = LocalstackBucketPair("source", "destination")

    monkeypatch.setattr(
        verify_module, "listed_keys", lambda *_args: set(archive_members) | direct_keys
    )
    monkeypatch.setattr(
        verify_module,
        "read_tar_gz_members_text",
        lambda _client, _bucket, key: {
            expectations_module.archive_member_name(source_key): f"payload for {source_key}\n"
            for source_key in archive_members[key]
        },
    )
    monkeypatch.setattr(
        verify_module,
        "read_tar_gz_member_pax_headers",
        lambda _client, _bucket, key: expectations_module.expected_pax_headers(
            archive_members[key]
        ),
    )
    monkeypatch.setattr(
        verify_module,
        "read_object_text",
        lambda _client, _bucket, key: f"payload for {source_by_destination[key]}\n",
    )

    verify_module.verify_demo_result(
        output=_output(archive_days),
        payload=payload,
        destination_client=cast(S3Client, object()),
        bucket_pair=pair,
        archive_days=archive_days,
        archive_members=archive_members,
        direct_keys=direct_keys,
        source_by_destination=source_by_destination,
        source_keys=source_keys,
        skipped_count=4,
    )


def test_verify_demo_result_reports_output_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    archive_days, archive_members, direct_keys, source_by_destination, source_keys = (
        _verify_inputs()
    )
    payload = _payload(archive_days, archive_members, direct_keys, source_keys)
    pair = LocalstackBucketPair("source", "destination")
    monkeypatch.setattr(
        verify_module, "listed_keys", lambda *_args: set(archive_members) | direct_keys
    )

    with pytest.raises(RuntimeError, match="did not contain"):
        verify_module.verify_demo_result(
            output="missing",
            payload=payload,
            destination_client=cast(S3Client, object()),
            bucket_pair=pair,
            archive_days=archive_days,
            archive_members=archive_members,
            direct_keys=direct_keys,
            source_by_destination=source_by_destination,
            source_keys=source_keys,
            skipped_count=4,
        )

    bad_payload = {**payload, "cleanup_preview": {}}
    with pytest.raises(RuntimeError, match="cleanup_preview"):
        verify_module.verify_demo_result(
            output=_output(archive_days),
            payload=bad_payload,
            destination_client=cast(S3Client, object()),
            bucket_pair=pair,
            archive_days=archive_days,
            archive_members=archive_members,
            direct_keys=direct_keys,
            source_by_destination=source_by_destination,
            source_keys=source_keys,
            skipped_count=4,
        )

    bad_result = dict(cast(dict[str, object], payload["archive_result"]))
    bad_result["archive_groups"] = [{"source_object_count": 2, "cleanup_status": "present"}]
    with pytest.raises(RuntimeError, match="cleanup_status"):
        verify_module.verify_demo_result(
            output=_output(archive_days),
            payload={**payload, "archive_result": bad_result},
            destination_client=cast(S3Client, object()),
            bucket_pair=pair,
            archive_days=archive_days,
            archive_members=archive_members,
            direct_keys=direct_keys,
            source_by_destination=source_by_destination,
            source_keys=source_keys,
            skipped_count=4,
        )


def test_verify_demo_result_reports_archive_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    archive_days, archive_members, direct_keys, source_by_destination, source_keys = (
        _verify_inputs()
    )
    pair = LocalstackBucketPair("source", "destination")
    monkeypatch.setattr(verify_module, "listed_keys", lambda *_args: {"wrong"})

    with pytest.raises(RuntimeError, match="destination object keys"):
        verify_module.verify_demo_result(
            output=_output(archive_days),
            payload=_payload(archive_days, archive_members, direct_keys, source_keys),
            destination_client=cast(S3Client, object()),
            bucket_pair=pair,
            archive_days=archive_days,
            archive_members=archive_members,
            direct_keys=direct_keys,
            source_by_destination=source_by_destination,
            source_keys=source_keys,
            skipped_count=4,
        )


def _verify_inputs() -> tuple[
    tuple[date, ...],
    dict[str, set[str]],
    set[str],
    dict[str, str],
    set[str],
]:
    archive_days = (date(2026, 2, 23),)
    archive_members = {
        "archive-a": {"plain-a", "C:/unsafe"},
        "archive-b": {"s3-archiver-safe/key", "plain-b"},
        "archive-c": {"plain-c", "plain-d"},
    }
    direct_keys = {"direct-a", "direct-b", "direct-c"}
    source_by_destination = {key: f"source-{key}" for key in direct_keys}
    source_keys = set().union(*archive_members.values(), set(source_by_destination.values()))
    source_keys |= {"skip-a", "skip-b", "skip-c", "skip-d"}
    return archive_days, archive_members, direct_keys, source_by_destination, source_keys


def _payload(
    archive_days: tuple[date, ...],
    archive_members: dict[str, set[str]],
    direct_keys: set[str],
    source_keys: set[str],
) -> dict[str, object]:
    return {
        "status": "ok",
        "archive_manifest": {
            "object_count": len(source_keys) - 4,
            "archive_days": [day.isoformat() for day in archive_days],
            "destination_archive_keys": sorted(archive_members),
            "destination_keys": sorted(set(archive_members) | direct_keys),
            "archive_count": len(archive_members),
            "direct_copy_count": len(direct_keys),
            "skipped_object_count": 4,
        },
        "archive_result": {
            "direct_copy_count": len(direct_keys),
            "archive_groups": [{"source_object_count": 2}],
        },
    }


def _output(archive_days: tuple[date, ...]) -> str:
    return "\n".join(
        (
            "== S3 Archiver Visual Demo ==",
            "== Archive Candidates ==",
            "archive day count: 365",
            f"archive day range: {min(archive_days)} through {max(archive_days)}",
            "archive root count: 6",
            "archive group count: 2190",
            "direct copy count: 2190",
            "source objects per archive: min=2 max=2",
        )
    )
