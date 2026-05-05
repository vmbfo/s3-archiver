"""Direct parser based on S3 object properties."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.results import SelectedObject

if TYPE_CHECKING:
    from s3_archiver_core.s3 import S3ListedObject


class DirectParser:
    """Select every listed object using the S3 last-modified timestamp."""

    @property
    def kind(self) -> ParserKind:
        return ParserKind.DIRECT

    def parse(self, listed: S3ListedObject) -> SelectedObject:
        """Select the listed object using its authoritative S3 timestamp."""

        return SelectedObject(
            timestamp=_as_utc(listed.last_modified),
            timestamp_source="last_modified",
            archive_root=_parent_prefix(listed.key),
        )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parent_prefix(key: str) -> str:
    parent, separator, _name = key.rpartition("/")
    return parent if separator else ""
