"""Filename/path timestamp parser."""

from __future__ import annotations

from s3_archiver_core.archive_timestamp import archive_root_for_key, select_key_timestamp
from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.results import SelectedObject, SkippedObject
from s3_archiver_core.s3 import S3ListedObject


class FilenameTimestampParser:
    """Select objects using timestamps embedded in source keys."""

    @property
    def kind(self) -> ParserKind:
        return ParserKind.FILENAME_TIMESTAMP

    def parse(self, listed: S3ListedObject) -> SelectedObject | SkippedObject:
        """Select the object when its key contains a reliable timestamp."""

        selected = select_key_timestamp(listed.key)
        if selected is None:
            return SkippedObject("no reliable key timestamp")
        timestamp, timestamp_source = selected
        return SelectedObject(timestamp, timestamp_source, archive_root_for_key(listed.key))
