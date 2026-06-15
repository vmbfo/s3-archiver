"""Tests for archive date-range bounds and env parsing."""

from __future__ import annotations

from datetime import date
from typing import Literal

import pytest
from s3_archiver_core._settings_parse import parse_archive_date_range_result
from s3_archiver_core.archive_date_range import ArchiveDateRange, parse_date_bound


@pytest.mark.unit()
@pytest.mark.parametrize(
    ("text", "bound", "expected"),
    [
        ("2019", "start", date(2019, 1, 1)),
        ("2019", "end", date(2019, 12, 31)),
        ("2019-02", "start", date(2019, 2, 1)),
        ("2019-02", "end", date(2019, 2, 28)),
        ("2020-02", "end", date(2020, 2, 29)),
        ("2019-01-10", "start", date(2019, 1, 10)),
        ("2019-01-10", "end", date(2019, 1, 10)),
        ("2019-01-01T10:00:00", "end", date(2019, 1, 1)),
        ("2019-01-01 10:00:00", "start", date(2019, 1, 1)),
        ("  2019-03  ", "start", date(2019, 3, 1)),
    ],
)
def test_parse_date_bound_expands_each_granularity(
    text: str, bound: Literal["start", "end"], expected: date
) -> None:
    assert parse_date_bound(text, bound=bound) == expected


@pytest.mark.unit()
@pytest.mark.parametrize("bound", ["start", "end"])
@pytest.mark.parametrize(
    "text",
    ["", "19", "2019-1", "2019-01-1", "2019-13", "2019-00", "2019-02-30", "abc", "2019-01-01-01"],
)
def test_parse_date_bound_rejects_malformed_values(
    text: str, bound: Literal["start", "end"]
) -> None:
    with pytest.raises(ValueError, match="date bound"):
        _ = parse_date_bound(text, bound=bound)


@pytest.mark.unit()
def test_includes_open_range_accepts_every_day() -> None:
    assert ArchiveDateRange().includes(date(1999, 1, 1)) is True


@pytest.mark.unit()
def test_includes_respects_both_bounds() -> None:
    window = ArchiveDateRange(date(2019, 1, 1), date(2020, 12, 31))
    assert window.includes(date(2019, 6, 15)) is True
    assert window.includes(date(2019, 1, 1)) is True
    assert window.includes(date(2020, 12, 31)) is True
    assert window.includes(date(2018, 12, 31)) is False
    assert window.includes(date(2021, 1, 1)) is False


@pytest.mark.unit()
def test_includes_open_low_and_open_high_bounds() -> None:
    open_low = ArchiveDateRange(end=date(2020, 1, 1))
    assert open_low.includes(date(1970, 1, 1)) is True
    assert open_low.includes(date(2020, 1, 2)) is False

    open_high = ArchiveDateRange(start=date(2020, 1, 1))
    assert open_high.includes(date(2999, 1, 1)) is True
    assert open_high.includes(date(2019, 12, 31)) is False


@pytest.mark.unit()
def test_parse_archive_date_range_result_unset_is_open_range() -> None:
    result = parse_archive_date_range_result({})
    assert result.ok
    assert result.value == ArchiveDateRange()


@pytest.mark.unit()
def test_parse_archive_date_range_result_reads_both_bounds() -> None:
    result = parse_archive_date_range_result({"ARCHIVER_FROM": "2019", "ARCHIVER_TO": "2020-06"})
    assert result.ok
    assert result.value == ArchiveDateRange(date(2019, 1, 1), date(2020, 6, 30))


@pytest.mark.unit()
def test_parse_archive_date_range_result_allows_single_open_bound() -> None:
    only_from = parse_archive_date_range_result({"ARCHIVER_FROM": "2019-01-02"})
    assert only_from.value == ArchiveDateRange(start=date(2019, 1, 2))

    only_to = parse_archive_date_range_result({"ARCHIVER_TO": "2019-01-02"})
    assert only_to.value == ArchiveDateRange(end=date(2019, 1, 2))


@pytest.mark.unit()
def test_parse_archive_date_range_result_ignores_blank_values() -> None:
    result = parse_archive_date_range_result({"ARCHIVER_FROM": "  ", "ARCHIVER_TO": ""})
    assert result.value == ArchiveDateRange()


@pytest.mark.unit()
@pytest.mark.parametrize("key", ["ARCHIVER_FROM", "ARCHIVER_TO"])
def test_parse_archive_date_range_result_reports_bad_bound(key: str) -> None:
    result = parse_archive_date_range_result({key: "not-a-date"})
    assert not result.ok
    assert result.issue is not None
    assert result.issue.field == key


@pytest.mark.unit()
def test_parse_archive_date_range_result_rejects_inverted_window() -> None:
    result = parse_archive_date_range_result({"ARCHIVER_FROM": "2020", "ARCHIVER_TO": "2019"})
    assert not result.ok
    assert result.issue is not None
    assert result.issue.field == "ARCHIVER_TO"
    assert "on or after" in result.issue.message
