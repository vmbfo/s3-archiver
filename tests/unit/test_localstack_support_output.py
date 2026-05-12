"""Unit tests for LocalStack support output and retry helpers."""

from __future__ import annotations

import pytest
from s3_archiver_localstack_support import (
    is_retryable_localstack_error,
    is_retryable_localstack_message,
    json_objects,
    last_json_object,
)

pytestmark = pytest.mark.unit()


def test_json_objects_extracts_object_lines_only() -> None:
    output = '\nplain\n{"status": "starting"}\n[1, 2]\n{"status": "ok", "count": 2}\n'

    assert json_objects(output) == [
        {"status": "starting"},
        {"status": "ok", "count": 2},
    ]
    assert last_json_object(output) == {"status": "ok", "count": 2}


def test_json_objects_extracts_json_line_from_mixed_stdout() -> None:
    assert json_objects('plain\n{"status":"ok"}\n') == [{"status": "ok"}]
    assert json_objects('plain\n{bad json}\n{"status":"ok"}\n') == [{"status": "ok"}]


def test_json_objects_accepts_whole_output_json_payloads() -> None:
    assert json_objects('{"status": "ok"}') == [{"status": "ok"}]
    assert json_objects('[{"status": "starting"}, {"status": "ok"}]') == [
        {"status": "starting"},
        {"status": "ok"},
    ]


def test_last_json_object_rejects_output_without_object_lines() -> None:
    with pytest.raises(ValueError, match="did not contain"):
        _ = last_json_object("plain\n[1, 2]\n")


def test_json_objects_falls_through_when_whole_output_json_is_malformed() -> None:
    assert json_objects('{malformed\n{"status":"ok"}\n') == [{"status": "ok"}]


def test_retryable_localstack_message_accepts_common_and_extra_fragments() -> None:
    assert is_retryable_localstack_message("Could not connect to the endpoint URL")
    assert is_retryable_localstack_message(
        "HeadObject failed: Not Found",
        extra_fragments=("HeadObject failed: Not Found",),
    )
    assert not is_retryable_localstack_message("AccessDenied")
    assert is_retryable_localstack_error(
        RuntimeError("Connection was closed before we received a valid response")
    )
