"""Parser names supported by route configuration."""

from __future__ import annotations

from typing import ClassVar


class ParserKind(str):
    """Configured parser name for selecting source objects."""

    DIRECT: ClassVar[ParserKind]
    FILENAME_TIMESTAMP: ClassVar[ParserKind]
    FOLDER_TIMESTAMP: ClassVar[ParserKind]

    def __new__(cls, value: str) -> ParserKind:
        return str.__new__(cls, value)

    @property
    def value(self) -> str:
        """Return the configured parser name."""

        return str(self)


ParserKind.DIRECT = ParserKind("direct")
ParserKind.FILENAME_TIMESTAMP = ParserKind("filename_timestamp")
ParserKind.FOLDER_TIMESTAMP = ParserKind("folder_timestamp")
