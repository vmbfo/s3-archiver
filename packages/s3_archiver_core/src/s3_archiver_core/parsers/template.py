"""Template parser for custom parser implementations.

This module is intentionally not registered in ``registry.py``.
"""

from __future__ import annotations

from s3_archiver_core.parsers.results import SkippedObject
from s3_archiver_core.s3 import S3ListedObject


class TemplateParser:
    """Copy-paste starting point for a custom parser."""

    def parse(self, _listed: S3ListedObject) -> SkippedObject:
        """Skip until the template is customized and registered."""

        return SkippedObject("template parser is not configured")
