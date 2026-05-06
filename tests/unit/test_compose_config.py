"""Unit tests for Docker Compose configuration semantics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit()
def test_compose_file_uses_readable_archiver_config_block() -> None:
    compose_text = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert compose_text.count("ARCHIVER_CONFIG_JSON: |") == 2
    app_routes = _compose_archiver_routes(compose_text, "app")
    scheduler_routes = _compose_archiver_routes(compose_text, "scheduler")

    assert scheduler_routes == app_routes
    assert len(app_routes) == 1
    route = app_routes[0]
    assert (route["name"], route["parser"], route["copy_mode"]) == (
        "default",
        "filename_timestamp",
        "daily_tar_gz",
    )
    assert route["source"] == {
        "provider": "$${S3_SOURCE_PROVIDER:-localstack}",
        "endpoint_url": "$${S3_SOURCE_ENDPOINT_URL:-http://localstack:4566}",
        "region": "$${S3_SOURCE_REGION:-us-east-1}",
        "bucket": "$${S3_SOURCE_BUCKET:-source-bucket}",
        "path": "$${S3_SOURCE_PATH:-}",
        "access_key_id": "$${S3_SOURCE_ACCESS_KEY_ID:-source-test}",
        "secret_access_key": "$${S3_SOURCE_SECRET_ACCESS_KEY:-source-test}",
        "addressing_style": "$${S3_SOURCE_ADDRESSING_STYLE:-path}",
    }
    assert route["destination"] == {
        "provider": "$${S3_DESTINATION_PROVIDER:-localstack}",
        "endpoint_url": "$${S3_DESTINATION_ENDPOINT_URL:-http://localstack:4566}",
        "region": "$${S3_DESTINATION_REGION:-us-east-1}",
        "bucket": "$${S3_DESTINATION_BUCKET:-destination-bucket}",
        "path": "$${S3_DESTINATION_PATH:-}",
        "access_key_id": "$${S3_DESTINATION_ACCESS_KEY_ID:-destination-test}",
        "secret_access_key": "$${S3_DESTINATION_SECRET_ACCESS_KEY:-destination-test}",
        "addressing_style": "$${S3_DESTINATION_ADDRESSING_STYLE:-path}",
    }
    assert "ARCHIVER_RETENTION_DAYS" not in compose_text
    assert "ARCHIVER_ENABLE_CLEANUP" not in compose_text
    assert "ARCHIVER_MAX_WORKERS" not in compose_text
    assert "S3_SOURCE_PATH_WHITELIST" not in compose_text
    assert "S3_SOURCE_PATH_BLACKLIST" not in compose_text


def _compose_archiver_routes(compose_text: str, service_name: str) -> list[dict[str, object]]:
    compose_config = cast(dict[str, object], yaml.safe_load(compose_text))
    services = cast(dict[str, object], compose_config["services"])
    service = cast(dict[str, object], services[service_name])
    environment = cast(dict[str, object], service["environment"])
    return cast(list[dict[str, object]], json.loads(cast(str, environment["ARCHIVER_CONFIG_JSON"])))
