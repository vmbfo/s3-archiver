"""Tests for ARCHIVER_FROM / ARCHIVER_TO settings and working-set reporting."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.route_payloads import working_set_payload
from s3_archiver_core.settings import AppSettings, ArchiveDateRange

from tests.unit.settings_fakes import dual_env


@pytest.mark.unit()
def test_from_env_defaults_to_open_archive_date_range(tmp_path: Path) -> None:
    settings = AppSettings.from_env(dual_env(tmp_path))

    assert settings.archive_date_range == ArchiveDateRange()


@pytest.mark.unit()
def test_from_env_reads_archive_date_range_bounds(tmp_path: Path) -> None:
    env = dual_env(tmp_path)
    env["ARCHIVER_FROM"] = "2019"
    env["ARCHIVER_TO"] = "2020-06"

    settings = AppSettings.from_env(env)

    assert settings.archive_date_range == ArchiveDateRange(date(2019, 1, 1), date(2020, 6, 30))


@pytest.mark.unit()
def test_working_set_payload_reports_archive_date_range(tmp_path: Path) -> None:
    env = dual_env(tmp_path)
    env["ARCHIVER_FROM"] = "2019-01-02"
    env["ARCHIVER_TO"] = "2020"

    payload = working_set_payload(AppSettings.from_env(env))

    assert payload["archive_from"] == "2019-01-02"
    assert payload["archive_to"] == "2020-12-31"


@pytest.mark.unit()
def test_working_set_payload_omits_unset_archive_date_range(tmp_path: Path) -> None:
    payload = working_set_payload(AppSettings.from_env(dual_env(tmp_path)))

    assert payload["archive_from"] is None
    assert payload["archive_to"] is None


@pytest.mark.unit()
@pytest.mark.parametrize("key", ["ARCHIVER_FROM", "ARCHIVER_TO"])
def test_from_env_rejects_malformed_archive_date_range(tmp_path: Path, key: str) -> None:
    env = dual_env(tmp_path)
    env[key] = "not-a-date"

    with pytest.raises(ConfigError, match=key):
        _ = AppSettings.from_env(env)


@pytest.mark.unit()
def test_from_env_rejects_inverted_archive_date_range(tmp_path: Path) -> None:
    env = dual_env(tmp_path)
    env["ARCHIVER_FROM"] = "2020"
    env["ARCHIVER_TO"] = "2019"

    with pytest.raises(ConfigError, match="on or after"):
        _ = AppSettings.from_env(env)
