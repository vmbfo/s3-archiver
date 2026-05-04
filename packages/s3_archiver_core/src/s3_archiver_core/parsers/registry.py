"""Parser registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from s3_archiver_core.parsers.direct import DirectParser
from s3_archiver_core.parsers.filename_timestamp import FilenameTimestampParser
from s3_archiver_core.parsers.folder_timestamp import FolderTimestampParser
from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.protocol import ObjectParser

ParserFactory = Callable[[], ObjectParser]

_REGISTRY: Mapping[ParserKind, ParserFactory] = {
    ParserKind.DIRECT: DirectParser,
    ParserKind.FILENAME_TIMESTAMP: FilenameTimestampParser,
    ParserKind.FOLDER_TIMESTAMP: FolderTimestampParser,
}


def parser_for_kind(kind: ParserKind) -> ObjectParser:
    """Return a new parser for a registered kind."""

    return _REGISTRY[kind]()


def registered_parser_kinds() -> frozenset[ParserKind]:
    """Return registered parser kinds."""

    return frozenset(_REGISTRY)
