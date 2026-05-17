"""Archive route construction helper tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from s3_archiver_core.archive_routes import archive_routes_from_settings
from s3_archiver_core.archive_s3 import S3ArchiveBucket
from s3_archiver_core.parsers import ParserKind
from s3_archiver_core.settings import (
    AppSettings,
    CopyMode,
    RouteSettings,
    S3AddressingStyle,
    S3LocationSettings,
    S3Provider,
)

pytestmark = pytest.mark.unit()


class _Client:
    pass


def test_archive_routes_from_settings_builds_runtime_bucket_adapters(tmp_path: Path) -> None:
    source = _location("source-bucket", "source/prefix", access_key_id="source-key")
    destination = _location(
        "destination-bucket",
        "destination/prefix",
        access_key_id="destination-key",
    )
    settings = AppSettings(
        run_timeout=timedelta(minutes=5),
        temp_dir=tmp_path / "tmp",
        log_level="INFO",
        log_dir=tmp_path / "logs",
        routes=(
            RouteSettings(
                name="daily",
                parser=ParserKind.FOLDER_TIMESTAMP,
                copy_mode=CopyMode.DAILY_TAR_GZ,
                source=source,
                destination=destination,
                copy_mode_group_after_timestamp_parts=1,
            ),
        ),
    )
    source_client = _Client()
    destination_client = _Client()
    calls: list[S3LocationSettings] = []

    def build_client(location: S3LocationSettings) -> object:
        calls.append(location)
        return source_client if location is source else destination_client

    routes = archive_routes_from_settings(settings, build_client)

    assert calls == [source, destination]
    assert len(routes) == 1
    route = routes[0]
    assert route.name == "daily"
    assert route.parser_kind == "folder_timestamp"
    assert route.copy_mode == "daily_tar_gz"
    assert route.copy_mode_group_after_timestamp_parts == 1
    assert route.source_path == "source/prefix"
    assert route.destination_path == "destination/prefix"
    assert route.source_identity == source.storage_identity()
    assert route.destination_identity == destination.storage_identity()
    assert route.transfer_capabilities.native_copy is False
    assert isinstance(route.source, S3ArchiveBucket)
    assert isinstance(route.destination, S3ArchiveBucket)
    assert route.source.client is source_client
    assert route.destination.client is destination_client
    assert route.source.bucket == "source-bucket"
    assert route.destination.bucket == "destination-bucket"
    assert route.source.temp_dir == settings.temp_dir
    assert route.destination.temp_dir == settings.temp_dir


def _location(bucket: str, path: str, *, access_key_id: str) -> S3LocationSettings:
    return S3LocationSettings(
        provider=S3Provider.LOCALSTACK,
        access_key_id=access_key_id,
        secret_access_key="secret",
        region="us-east-1",
        bucket=bucket,
        namespace=None,
        iam_user_ocid=None,
        endpoint_url="http://localstack:4566",
        addressing_style=S3AddressingStyle.PATH,
        path=path,
    )
