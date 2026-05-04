"""Parser kinds supported by route configuration."""

from __future__ import annotations

from enum import StrEnum


class ParserKind(StrEnum):
    """Configured parser choices for selecting source objects."""

    DIRECT = "direct"
    FILENAME_TIMESTAMP = "filename_timestamp"
    FOLDER_TIMESTAMP = "folder_timestamp"
