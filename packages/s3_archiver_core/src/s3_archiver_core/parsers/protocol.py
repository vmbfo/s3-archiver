"""Parser protocol."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from s3_archiver_core.parsers.results import SelectedObject, SkippedObject


class ParserObjectProperties(Protocol):
    """Structural object properties needed by parsers."""

    @property
    def size(self) -> int:
        """Return object size."""
        ...

    @property
    def last_modified(self) -> datetime | None:
        """Return object last modified time when available."""
        ...

    @property
    def metadata(self) -> Mapping[str, str]:
        """Return user metadata."""
        ...

    @property
    def tags(self) -> Mapping[str, str]:
        """Return object tags."""
        ...

    @property
    def checksums(self) -> Mapping[str, str]:
        """Return object checksum values."""
        ...


class ParserListedObject(Protocol):
    """Structural listed-object boundary needed by parsers."""

    @property
    def key(self) -> str:
        """Return object key."""
        ...

    @property
    def size(self) -> int:
        """Return listed object size."""
        ...

    @property
    def last_modified(self) -> datetime:
        """Return listed object last modified time."""
        ...

    @property
    def version_id(self) -> str | None:
        """Return object version id."""
        ...

    @property
    def properties(self) -> ParserObjectProperties:
        """Return listed object properties."""
        ...


@dataclass(frozen=True, slots=True)
class ParserContext:
    """Optional S3 metadata boundary available to object parsers."""

    listed: ParserListedObject
    properties: ParserObjectProperties | None = None


class ObjectParser(Protocol):
    """Select or skip one listed S3 object."""

    def parse(
        self, listed: ParserListedObject, context: ParserContext
    ) -> SelectedObject | SkippedObject:
        """Parse one listed object."""
        ...
