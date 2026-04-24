"""Unit tests for archive transfer strategy selection."""

from __future__ import annotations

import pytest
from s3_archiver_core.archive_transfer import select_transfer_strategy
from s3_archiver_core.s3 import S3TransferCapabilities


@pytest.mark.unit()
def test_transfer_strategy_prefers_native_copy_then_streaming_then_temp_file() -> None:
    assert (
        select_transfer_strategy(10, S3TransferCapabilities(), simple_copy_limit_bytes=10)
        == "simple_native_copy"
    )
    assert (
        select_transfer_strategy(11, S3TransferCapabilities(), simple_copy_limit_bytes=10)
        == "multipart_native_copy"
    )
    assert (
        select_transfer_strategy(
            11,
            S3TransferCapabilities(native_copy=False),
            simple_copy_limit_bytes=10,
        )
        == "multipart_streaming"
    )
    assert (
        select_transfer_strategy(
            51,
            S3TransferCapabilities(
                native_copy=False,
                streaming_upload=True,
                streaming_limit_bytes=50,
            ),
            streaming_limit_bytes=50,
        )
        == "temp_file_backed"
    )
    assert (
        select_transfer_strategy(
            6,
            S3TransferCapabilities(
                native_copy=False,
                multipart_copy=False,
                streaming_upload=True,
                streaming_limit_bytes=5,
            ),
        )
        == "temp_file_backed"
    )


@pytest.mark.unit()
def test_transfer_strategy_rejects_missing_fallback_capability() -> None:
    with pytest.raises(ValueError, match="no supported transfer strategy"):
        _ = select_transfer_strategy(
            99,
            S3TransferCapabilities(
                native_copy=False,
                multipart_copy=False,
                streaming_upload=False,
                temp_file_backed=False,
            ),
        )
