"""Unit tests for archive option parsing."""

from __future__ import annotations

from datetime import timedelta

import pytest
from s3_archiver_core.archive_options import ArchiveOptions, cleanup_enabled_from_env


@pytest.mark.unit()
def test_options_cleanup_defaults() -> None:
    assert cleanup_enabled_from_env({}) is False
    assert cleanup_enabled_from_env({"ARCHIVER_ENABLE_CLEANUP": "true"}) is True
    assert ArchiveOptions.from_env({}).run_timeout == timedelta(days=7)
