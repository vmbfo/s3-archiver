"""Direct parser based on S3 object properties."""

from __future__ import annotations

from datetime import UTC, datetime

from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.protocol import (
    ParserContext,
    ParserListedObject,
    ParserObjectProperties,
)
from s3_archiver_core.parsers.results import SelectedObject


class DirectParser:
    """Select every listed object using authoritative S3 listing properties.

    The parser owns direct-mode timestamp/root selection. The manifest carries
    the original listed object forward so copy and verification retain the full
    S3 property set.
    """

    @property
    def kind(self) -> ParserKind:
        return ParserKind.DIRECT

    def parse(
        self, listed: ParserListedObject, context: ParserContext | None = None
    ) -> SelectedObject:
        """Select the listed object using its authoritative S3 metadata."""

        properties = _context_properties(listed, context)
        return SelectedObject(
            timestamp=_as_utc(properties.last_modified or listed.last_modified),
            timestamp_source="last_modified",
            archive_root=_parent_prefix(listed.key),
        )


def _context_properties(
    listed: ParserListedObject, context: ParserContext | None
) -> ParserObjectProperties:
    if context is None:
        return listed.properties
    if context.listed.key != listed.key or context.listed.version_id != listed.version_id:
        raise ValueError("parser context does not match listed object")
    if context.properties is None:
        return listed.properties
    if context.properties.size != listed.size:
        raise ValueError("listed object size differs from hydrated properties")
    return context.properties


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parent_prefix(key: str) -> str:
    parent, separator, _name = key.rpartition("/")
    return parent if separator else ""


Parser = DirectParser
