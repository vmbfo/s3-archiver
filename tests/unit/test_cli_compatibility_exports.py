"""Compatibility export tests for legacy CLI helper modules."""

from __future__ import annotations

import pytest
from s3_archiver_cli import _archive_routes, route_payloads
from s3_archiver_core import archive_routes as core_archive_routes
from s3_archiver_core import route_payloads as core_route_payloads

pytestmark = pytest.mark.unit()


def test_archive_route_exports_match_core_helpers() -> None:
    assert _archive_routes.BuildS3Client is core_archive_routes.BuildS3Client
    assert (
        _archive_routes.archive_routes_from_settings
        is core_archive_routes.archive_routes_from_settings
    )


def test_route_payload_exports_match_core_helpers() -> None:
    assert route_payloads.route_payloads is core_route_payloads.route_payloads
    assert route_payloads.route_summary_payload is core_route_payloads.route_summary_payload
