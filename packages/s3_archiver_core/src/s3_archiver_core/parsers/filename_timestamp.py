"""Filename/path timestamp parser and key timestamp helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import PurePosixPath

from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.protocol import ParserContext, ParserListedObject
from s3_archiver_core.parsers.results import SelectedObject, SkippedObject, TimestampSource

_DATE = r"(?P<year>20\d{2})[-_/]?(?P<month>0[1-9]|1[0-2])[-_/]?(?P<day>0[1-9]|[12]\d|3[01])"
_TIME = r"(?P<hour>[01]\d|2[0-3])(?P<tsep>[-:]?)(?P<minute>[0-5]\d)(?P=tsep)(?P<second>[0-5]\d)"
_DATE_RE = re.compile(_DATE)
_TIME_RE = re.compile(rf"{_TIME}(?P<zone>Z|[+-](?:[01]\d|2[0-3]):?[0-5]\d)?")


@dataclass(frozen=True, slots=True)
class _TimestampCandidate:
    value: datetime
    source: TimestampSource
    text: str


@dataclass(frozen=True, slots=True)
class _CandidateScan:
    candidates: tuple[_TimestampCandidate, ...]
    malformed: bool


@dataclass(frozen=True, slots=True)
class _PathTimestampSpan:
    candidate: _TimestampCandidate
    part_start: int
    part_count: int


class _MalformedTimestampError(ValueError):
    pass


class FilenameTimestampParser:
    @property
    def kind(self) -> ParserKind:
        return ParserKind.FILENAME_TIMESTAMP

    def parse(
        self, listed: ParserListedObject, context: ParserContext | None = None
    ) -> SelectedObject | SkippedObject:
        _ = context
        selected = select_key_timestamp(listed.key)
        if selected is None:
            return SkippedObject("no reliable key timestamp")
        timestamp, timestamp_source = selected
        return SelectedObject(timestamp, timestamp_source, archive_root_for_key(listed.key))


def select_key_timestamp(
    key: str, *, last_modified: datetime | None = None
) -> tuple[datetime, TimestampSource] | None:
    _ = last_modified
    path = PurePosixPath(key)
    basename_scan = _candidate_scan(path.name, "basename")
    basename_candidates = basename_scan.candidates
    path_candidates = _path_candidates(path)
    if basename_candidates:
        return _pick_candidate(basename_candidates, path_candidates)
    if basename_scan.malformed:
        return None
    if path_candidates:
        candidate = _latest_candidate(path_candidates)
        return candidate.value, candidate.source
    return None


def select_folder_timestamp(key: str) -> tuple[datetime, TimestampSource] | None:
    candidates = _path_candidates(PurePosixPath(key))
    if not candidates:
        return None
    candidate = _latest_candidate(candidates)
    return candidate.value, candidate.source


def archive_root_for_key(key: str) -> str:
    parts = list(PurePosixPath(key).parent.parts)
    while parts:
        stripped = _strip_one_timestamp_suffix(parts)
        if len(stripped) == len(parts):
            break
        parts = stripped
    return "/".join(part for part in parts if part not in {"", "."})


def grouped_archive_root_after_folder_timestamp(
    key: str, group_after_timestamp_parts: int
) -> str | None:
    if group_after_timestamp_parts <= 0:
        return None
    parts = _clean_path_parts(PurePosixPath(key).parent.parts)
    span = _latest_path_timestamp_span(parts)
    if span is None:
        return None
    root_end = span.part_start + span.part_count + group_after_timestamp_parts
    if len(parts) < root_end:
        return None
    return "/".join(parts[span.part_start : root_end])


def destination_archive_key(archive_root: str, target_day: date) -> str:
    filename = f"{target_day.isoformat()}.tar.gz"
    return f"{archive_root}/{filename}" if archive_root else filename


def _pick_candidate(
    basename_candidates: tuple[_TimestampCandidate, ...],
    path_candidates: tuple[_TimestampCandidate, ...],
) -> tuple[datetime, TimestampSource]:
    path_values = {candidate.value for candidate in path_candidates}
    candidate = max(
        basename_candidates,
        key=lambda item: (
            1 if item.value in path_values else 0,
            item.value,
        ),
    )
    return candidate.value, candidate.source


def _latest_candidate(candidates: tuple[_TimestampCandidate, ...]) -> _TimestampCandidate:
    return max(candidates, key=lambda candidate: candidate.value)


def _path_candidates(path: PurePosixPath) -> tuple[_TimestampCandidate, ...]:
    parts = _clean_path_parts(path.parent.parts)
    parent = "/".join(parts)
    span_candidates = tuple(span.candidate for span in _path_timestamp_spans(parts))
    return _dedupe((*_candidates(parent, "path"), *span_candidates))


def _candidates(text: str, source: TimestampSource) -> tuple[_TimestampCandidate, ...]:
    return _candidate_scan(text, source).candidates


def _candidate_scan(text: str, source: TimestampSource) -> _CandidateScan:
    candidates: list[_TimestampCandidate] = []
    malformed = False
    for match in _DATE_RE.finditer(text):
        try:
            candidate = _match_datetime(text, match, source)
        except ValueError:
            malformed = True
            continue
        candidates.append(candidate)
    return _CandidateScan(_dedupe(tuple(candidates)), malformed)


def _latest_path_timestamp_span(parts: tuple[str, ...]) -> _PathTimestampSpan | None:
    spans = _path_timestamp_spans(parts)
    if not spans:
        return None
    return max(spans, key=lambda span: span.candidate.value)


def _path_timestamp_spans(parts: tuple[str, ...]) -> tuple[_PathTimestampSpan, ...]:
    candidates: list[_PathTimestampSpan] = []
    for index, part in enumerate(parts):
        candidates.extend(
            _PathTimestampSpan(candidate, index, 1) for candidate in _candidates(part, "path")
        )
    for index in range(len(parts) - 2):
        span = _segmented_path_span(parts, index)
        if span is not None:
            candidates.append(span)
    return tuple(dict.fromkeys(candidates))


def _segmented_path_span(parts: tuple[str, ...], index: int) -> _PathTimestampSpan | None:
    year, month, day = parts[index : index + 3]
    hour = parts[index + 3] if index + 3 < len(parts) else None
    if not (_is_year(year) and _is_month(month) and _is_day(day)):
        return None
    hour_value = int(hour) if hour is not None and _is_hour(hour) else 0
    part_count = 4 if hour is not None and _is_hour(hour) else 3
    try:
        dt = datetime(int(year), int(month), int(day), hour_value, tzinfo=UTC)
    except ValueError:
        return None
    return _PathTimestampSpan(
        _TimestampCandidate(dt, "path", "/".join(parts[index : index + part_count])),
        index,
        part_count,
    )


def _match_datetime(
    text: str, date_match: re.Match[str], source: TimestampSource
) -> _TimestampCandidate:
    tail = text[date_match.end() :]
    time_start = _time_start(tail)
    if time_start is None:
        dt = datetime(
            int(date_match["year"]), int(date_match["month"]), int(date_match["day"]), tzinfo=UTC
        )
        return _TimestampCandidate(dt, source, date_match.group(0))

    time_match = _TIME_RE.match(tail, time_start)
    if time_match is None:
        raise _MalformedTimestampError
    if _has_unconsumed_timezone_suffix(tail[time_match.end() :]):
        raise _MalformedTimestampError
    dt = datetime(
        int(date_match["year"]),
        int(date_match["month"]),
        int(date_match["day"]),
        int(time_match["hour"]),
        int(time_match["minute"]),
        int(time_match["second"]),
        tzinfo=_timezone(time_match["zone"]),
    ).astimezone(UTC)
    consumed = date_match.end() + time_match.end()
    return _TimestampCandidate(dt, source, text[date_match.start() : consumed])


def _has_unconsumed_timezone_suffix(value: str) -> bool:
    if not value:
        return False
    if value[0] in {"+", "Z"}:
        return True
    return value[0] == "-" and len(value) > 1 and (value[1].isdigit() or value[1] == ".")


def _time_start(tail: str) -> int | None:
    if not tail:
        return None
    if tail[0] in {"T", "_", " "}:
        return 1
    if tail[0] == "-" and len(tail) > 1 and tail[1].isdigit():
        return 1
    if tail[0].isdigit():
        return 0
    return None


def _timezone(value: str | None) -> timezone:
    if value is None or value == "Z":
        return UTC
    sign = 1 if value[0] == "+" else -1
    offset = value[1:].replace(":", "")
    hours = int(offset[:2])
    minutes = int(offset[2:])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _strip_one_timestamp_suffix(parts: list[str]) -> list[str]:
    if (
        len(parts) >= 4
        and _is_year(parts[-4])
        and _is_month(parts[-3])
        and _is_day(parts[-2])
        and _is_hour(parts[-1])
    ):
        return parts[:-4]
    if len(parts) >= 3 and _is_year(parts[-3]) and _is_month(parts[-2]) and _is_day(parts[-1]):
        return parts[:-3]
    if _candidates(parts[-1], "path"):
        return parts[:-1]
    return parts


def _dedupe(candidates: tuple[_TimestampCandidate, ...]) -> tuple[_TimestampCandidate, ...]:
    return tuple(dict.fromkeys(candidates))


def _clean_path_parts(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part for part in parts if part not in {"", "."})


def _is_year(value: str | None) -> bool:
    return value is not None and re.fullmatch(r"20\d{2}", value) is not None


def _is_month(value: str | None) -> bool:
    return value is not None and re.fullmatch(r"0[1-9]|1[0-2]", value) is not None


def _is_day(value: str | None) -> bool:
    return value is not None and re.fullmatch(r"0[1-9]|[12]\d|3[01]", value) is not None


def _is_hour(value: str | None) -> bool:
    return value is not None and re.fullmatch(r"[01]\d|2[0-3]", value) is not None


Parser = FilenameTimestampParser
