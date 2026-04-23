"""Unit tests for archive option parsing."""

from __future__ import annotations

from datetime import timedelta

import pytest
from s3_archiver_core.archive_options import ArchiveOptions, cleanup_enabled_from_env
from s3_archiver_core.errors import ConfigError


@pytest.mark.unit()
def test_options_cleanup_defaults() -> None:
    assert cleanup_enabled_from_env({}) is False
    assert cleanup_enabled_from_env({"ARCHIVER_ENABLE_CLEANUP": "true"}) is True
    assert ArchiveOptions.from_env({}).run_timeout == timedelta(days=7)


@pytest.mark.unit()
def test_options_reject_invalid_env_values() -> None:
    with pytest.raises(ConfigError, match="ARCHIVER_ENABLE_CLEANUP"):
        _ = cleanup_enabled_from_env({"ARCHIVER_ENABLE_CLEANUP": "yes"})

    with pytest.raises(ConfigError, match="ARCHIVER_MAX_WORKERS"):
        _ = ArchiveOptions.from_env({"ARCHIVER_MAX_WORKERS": "0"})

    with pytest.raises(ConfigError, match="ARCHIVER_RUN_TIMEOUT"):
        _ = ArchiveOptions.from_env({"ARCHIVER_RUN_TIMEOUT": "soon"})
