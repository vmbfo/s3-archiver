"""Unit tests for Docker Compose configuration semantics."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.unit()
def test_compose_file_delegates_config_to_env_file() -> None:
    compose_text = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    compose_config = cast(dict[str, object], yaml.safe_load(compose_text))
    services = cast(dict[str, object], compose_config["services"])

    for service_name in ("app", "scheduler"):
        service = cast(dict[str, object], services[service_name])
        environment = cast(dict[str, object], service["environment"])
        assert "ARCHIVER_CONFIG_JSON" not in environment
        assert "S3_ACCESS_KEY" not in environment
        assert "S3_SECRET_KEY" not in environment
        assert "S3_ENDPOINT" not in environment
    assert "ARCHIVER_RETENTION_DAYS" not in compose_text
    assert "ARCHIVER_ENABLE_CLEANUP" not in compose_text
    assert "ARCHIVER_MAX_WORKERS" not in compose_text
    assert "S3_SOURCE_PATH_WHITELIST" not in compose_text
    assert "S3_SOURCE_PATH_BLACKLIST" not in compose_text
