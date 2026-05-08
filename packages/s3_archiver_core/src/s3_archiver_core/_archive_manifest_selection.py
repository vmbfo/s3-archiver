from __future__ import annotations

from collections.abc import Callable
from inspect import Parameter, signature
from typing import cast

from s3_archiver_core._archive_manifest_models import (
    ParserKind,
    ParserResult,
    ParserSelector,
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
    parser: ParserSelector | None,
    listed: S3ListedObject,
    source_path: str,
) -> SelectedObject | SkippedObject | ParserSkippedObject | None:
    object_parser = None if parser is not None else parser_for_kind(parser_kind)
    context = ParserContext(listed, listed.properties)
    try:
        if parser is not None:
            return _call_parser_selector(parser, listed, context)
        assert object_parser is not None
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


def _call_parser_selector(
    parser: ParserSelector, listed: S3ListedObject, context: ParserContext
) -> ParserResult:
    if _accepts_context(parser):
        parser_with_context = cast(Callable[[S3ListedObject, ParserContext], ParserResult], parser)
        return parser_with_context(listed, context)
    parser_without_context = cast(Callable[[S3ListedObject], ParserResult], parser)
    return parser_without_context(listed)


def _accepts_context(parser: ParserSelector) -> bool:
    try:
        parameters = signature(parser).parameters.values()
    except (TypeError, ValueError):
        return False
    positional_count = 0
    for parameter in parameters:
        if parameter.kind is Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in {
            Parameter.POSITIONAL_ONLY,
            Parameter.POSITIONAL_OR_KEYWORD,
        }:
            positional_count += 1
    return positional_count >= 2
