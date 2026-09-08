"""An archive conflict must not block verified groups on the same route."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from s3_archiver_cli.cleanup_commands import export_and_chain_cleanup, write_result_manifest
from s3_archiver_core.cleanup_manifest import iter_cleanup_records

from tests.unit.archive_workflow_fakes import (
    FakeBucket,
    archive_routes,
    listed_object,
    object_properties,
)
from tests.unit.cli_cleanup_test_support import make_settings
from tests.unit.test_archive_refresh_safety import run


@pytest.mark.unit()
def test_partial_run_exports_and_cleans_only_verified_groups(
    tmp_path: Path, base_env: dict[str, str]
) -> None:
    good = listed_object("data/good/2026-05-21T00-00-00Z.grib", 1)
    bad = listed_object("data/bad/2026-05-21T00-00-00Z.grib", 1)
    source = FakeBucket("source", (good, bad), temp_dir=tmp_path)
    destination = FakeBucket(
        "destination",
        temp_dir=tmp_path,
        destination={"data/bad/2026-05-21.tar.gz": object_properties(metadata={"bad": "data"})},
    )
    result = run(source, destination)
    assert not result.ok
    assert result.verify.ok and not result.verify.skipped
    settings = make_settings(tmp_path, base_env, cleanup=True)
    path = write_result_manifest(settings, result)
    assert path is not None
    assert [record.key for record in iter_cleanup_records(path)] == [good.key]
    payload = export_and_chain_cleanup(
        settings,
        (
            replace(
                archive_routes(source, destination)[0],
                source_identity=result.manifest.entries[0].source_identity,
            ),
        ),
        result,
        datetime.now(tz=UTC),
        tmp_path / "log",
    )
    assert payload is not None
    assert source.deleted == [(good.key, good.version_id)]
    assert source.head_object(bad.key, bad.version_id) is not None
    assert not path.exists()
