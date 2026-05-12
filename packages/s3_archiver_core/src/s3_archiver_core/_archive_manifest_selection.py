from __future__ import annotations

from s3_archiver_core._archive_manifest_models import (
    ParserKind,
    SelectedObject,
    SkippedObject,
)
from s3_archiver_core._archive_manifest_paths import as_utc, relative_archive_root
from s3_archiver_core.parsers.protocol import ParserContext
from s3_archiver_core.parsers.registry import parser_for_kind
from s3_archiver_core.parsers.results import SkippedObject as ParserSkippedObject
from s3_archiver_core.s3 import S3ListedObject


def select_object(
    parser_kind: ParserKind,
    listed: S3ListedObject,
    source_path: str,
) -> SelectedObject | SkippedObject | ParserSkippedObject:
    object_parser = parser_for_kind(parser_kind)
    context = ParserContext(listed, listed.properties)
    try:
        result = object_parser.parse(listed, context)
    except ValueError as exc:
        return SkippedObject(listed.key, f"parser error: {exc}")
    if isinstance(result, ParserSkippedObject):
        return SkippedObject(listed.key, result.reason)
    return SelectedObject(
        as_utc(result.timestamp),
        result.timestamp_source,
        relative_archive_root(result.archive_root, source_path),
    )
