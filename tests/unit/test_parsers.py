"""Tests for object parsers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from s3_archiver_core.parsers import SelectedObject, SkippedObject
from s3_archiver_core.parsers.direct import DirectParser
from s3_archiver_core.parsers.filename_timestamp import FilenameTimestampParser
from s3_archiver_core.parsers.folder_timestamp import FolderTimestampParser
from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.registry import parser_for_kind, registered_parser_kinds
from s3_archiver_core.parsers.template import TemplateParser
from s3_archiver_core.s3 import S3ListedObject, S3ObjectProperties


def _listed(key: str, last_modified: datetime | None = None) -> S3ListedObject:
    timestamp = last_modified or datetime(2026, 4, 20, 12, tzinfo=UTC)
    properties = S3ObjectProperties(
        size=10,
        etag='"etag"',
        content_type="text/plain",
        content_encoding=None,
        content_language=None,
        content_disposition=None,
        cache_control=None,
        expires=None,
        metadata={"owner": "archive"},
        tags={"kind": "source"},
        last_modified=timestamp,
    )
    return S3ListedObject(
        key=key,
        size=10,
        last_modified=timestamp,
        etag='"etag"',
        version_id="v1",
        properties=properties,
    )


@pytest.mark.unit()
def test_registry_contains_only_supported_parser_kinds() -> None:
    assert registered_parser_kinds() == {
        ParserKind.DIRECT,
        ParserKind.FILENAME_TIMESTAMP,
        ParserKind.FOLDER_TIMESTAMP,
    }
    assert isinstance(parser_for_kind(ParserKind.DIRECT), DirectParser)
    assert ParserKind("direct") in registered_parser_kinds()
    assert DirectParser().kind is ParserKind.DIRECT
    assert FilenameTimestampParser().kind is ParserKind.FILENAME_TIMESTAMP
    assert FolderTimestampParser().kind is ParserKind.FOLDER_TIMESTAMP
    assert TemplateParser().parse(_listed("data/file.txt")).reason == (
        "template parser is not configured"
    )


@pytest.mark.unit()
def test_direct_parser_selects_s3_last_modified_timestamp() -> None:
    listed = _listed("data/fae/file.txt", datetime(2026, 4, 13, 8))

    selected = DirectParser().parse(listed)

    assert selected == SelectedObject(
        datetime(2026, 4, 13, 8, tzinfo=UTC),
        "last_modified",
        "data/fae",
    )


@pytest.mark.unit()
def test_filename_parser_selects_key_timestamp_without_s3_fallback() -> None:
    parser = FilenameTimestampParser()

    selected = parser.parse(
        _listed("data/fae/2026-04-13T07-00-00Z.xml", datetime(1999, 1, 1, tzinfo=UTC))
    )
    skipped = parser.parse(_listed("data/fae/no-stamp.xml"))

    assert selected == SelectedObject(
        datetime(2026, 4, 13, 7, tzinfo=UTC),
        "basename",
        "data/fae",
    )
    assert skipped == SkippedObject("no reliable key timestamp")


@pytest.mark.unit()
def test_folder_parser_selects_folder_timestamp_without_basename_fallback() -> None:
    parser = FolderTimestampParser()

    folder_selected = parser.parse(_listed("data/fae/2026/04/13/07/no-stamp.xml"))
    basename_only = parser.parse(_listed("data/fae/2026-04-13T07-00-00Z.xml"))

    assert folder_selected == SelectedObject(
        datetime(2026, 4, 13, 7, tzinfo=UTC),
        "path",
        "data/fae",
    )
    assert basename_only == SkippedObject("no reliable folder timestamp")
