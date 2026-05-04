"""Parser protocol."""

from __future__ import annotations

from typing import Protocol

from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.results import SelectedObject, SkippedObject
from s3_archiver_core.s3 import S3ListedObject


class ObjectParser(Protocol):
    """Select or skip one listed S3 object."""

    @property
    def kind(self) -> ParserKind:
        """Return the parser kind."""
        ...

    def parse(self, listed: S3ListedObject) -> SelectedObject | SkippedObject:
        """Parse one listed object."""
        ...
