"""Route copy-mode configuration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.settings import AppSettings, CopyMode


@pytest.mark.unit()
def test_from_env_decodes_object_copy_mode_options(tmp_path: Path) -> None:
    settings = AppSettings.from_env(
        _env(
            tmp_path,
            [
                _route(
                    parser="folder_timestamp",
                    copy_mode={
                        "type": "daily_tar_gz",
                        "group_after_timestamp_parts": 1,
                    },
                )
            ],
        )
    )

    route = settings.routes[0]
    assert route.copy_mode is CopyMode.DAILY_TAR_GZ
    assert route.copy_mode_group_after_timestamp_parts == 1


@pytest.mark.unit()
def test_from_env_rejects_grouped_copy_mode_for_non_folder_parser(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="folder_timestamp"):
        _ = AppSettings.from_env(
            _env(
                tmp_path,
                [
                    _route(
                        parser="filename_timestamp",
                        copy_mode={
                            "type": "daily_tar_gz",
                            "group_after_timestamp_parts": 1,
                        },
                    )
                ],
            )
        )


@pytest.mark.unit()
@pytest.mark.parametrize(
    "copy_mode",
    [
        {"type": "unknown", "group_after_timestamp_parts": 1},
        {"type": "daily_tar_gz", "group_after_timestamp_parts": -1},
        {"type": "daily_tar_gz", "group_after_timestamp_parts": 1.5},
        {"type": "daily_tar_gz", "group_after_timestamp_parts": True},
    ],
)
def test_from_env_rejects_invalid_object_copy_mode_options(
    tmp_path: Path, copy_mode: dict[str, object]
) -> None:
    with pytest.raises(ConfigError, match="copy_mode"):
        _ = AppSettings.from_env(_env(tmp_path, [_route(copy_mode=copy_mode)]))


def _route(
    *,
    parser: str = "filename_timestamp",
    copy_mode: object = "daily_tar_gz",
) -> dict[str, object]:
    return {
        "name": "fae-daily",
        "parser": parser,
        "copy_mode": copy_mode,
        "source": {
            "provider": "localstack",
            "endpoint_url": "http://localstack:4566/",
            "region": "us-east-1",
            "bucket": "source-bucket",
            "path": "data/fae/",
            "access_key_id": "${S3_SOURCE_ACCESS_KEY_ID}",
            "secret_access_key": "${S3_SOURCE_SECRET_ACCESS_KEY}",
            "addressing_style": "path",
        },
        "destination": {
            "provider": "localstack",
            "endpoint_url": "http://localhost:4566",
            "region": "us-east-1",
            "bucket": "archive-bucket",
            "path": "archives/fae/",
            "access_key_id": "destination-access",
            "secret_access_key": "destination-secret",
            "addressing_style": "path",
        },
    }


def _env(tmp_path: Path, routes: list[dict[str, object]]) -> dict[str, str]:
    return {
        "ARCHIVER_CONFIG_JSON": json.dumps(routes),
        "S3_SOURCE_ACCESS_KEY_ID": "source-access",
        "S3_SOURCE_SECRET_ACCESS_KEY": "source-secret",
        "LOG_DIR": str(tmp_path / "logs"),
    }
