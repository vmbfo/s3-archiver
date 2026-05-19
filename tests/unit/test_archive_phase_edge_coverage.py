"""Focused coverage tests for archive phase edge paths."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from s3_archiver_core import _archive_copy as archive_copy_module
from s3_archiver_core._archive_copy import copy_phase, verify_phase
from s3_archiver_core._archive_parallel import run_parallel_items
from s3_archiver_core._archive_protocols import ArchiveBucket
from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRoute
from s3_archiver_core.archive_manifest import (
    ArchiveGroup,
    ArchiveManifest,
    ManifestEntry,
    build_archive_manifest,
)
from s3_archiver_core.archive_routes import DebugLogger

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed
from tests.unit.archive_workflow_fakes import object_properties as _properties


@pytest.mark.unit()
def test_verify_phase_reports_archive_verification_failure() -> None:
    source = FakeBucket("source", (_listed("data/fae/2026-04-13T00-00-00Z.txt", 1),))
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=datetime(2026, 4, 27, 12, tzinfo=UTC),
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
    )
    group = manifest.archive_groups[0]
    destination = FakeBucket(
        "destination",
        destination={group.destination_archive_key: _properties(metadata={})},
    )

    result = verify_phase(
        (group,),
        (),
        {
            "default": ArchiveRoute(
                "default",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
            )
        },
        lambda: False,
        lambda: 1.0,
    )

    assert result.failures == ("data/fae/2026-04-13.tar.gz: archive verification failed",)


@pytest.mark.unit()
def test_verify_phase_compatibility_accepts_route_map() -> None:
    source = FakeBucket("source", (_listed("data/fae/2026-04-13T00-00-00Z.txt", 1),))
    destination = FakeBucket("destination")
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=datetime(2026, 4, 27, 12, tzinfo=UTC),
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
    )
    result = verify_phase(
        manifest.archive_groups,
        (),
        {
            "default": ArchiveRoute(
                "default",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
            )
        },
        lambda: False,
        lambda: 1.0,
    )

    assert result.failures == ("data/fae/2026-04-13.tar.gz: destination missing",)


@pytest.mark.unit()
def test_route_worker_edges_cover_empty_timeout_and_worker_exception() -> None:
    run_parallel_items = _run_parallel_items()
    assert run_parallel_items((), lambda _route: (), lambda: False, lambda: 1.0) == ()
    assert run_parallel_items(("route",), lambda _route: (), lambda: True, lambda: 1.0) == (
        "archive run timed out",
    )

    def fail_worker(_route: str) -> tuple[str, ...]:
        raise RuntimeError("route boom")

    assert run_parallel_items(("route",), fail_worker, lambda: False, lambda: 1.0) == (
        "route: route boom",
    )


@pytest.mark.unit()
def test_copy_phase_handles_manifest_with_no_configured_routes() -> None:
    manifest = ArchiveManifest(
        datetime(2026, 4, 27, 12, tzinfo=UTC),
        (),
        None,
        (),
        (),
    )

    phase, groups, entries = copy_phase(manifest, {}, None, lambda: False, lambda: 1.0)

    assert phase.ok is True
    assert groups == ()
    assert entries == ()


@pytest.mark.unit()
def test_copy_phase_ignores_uncopied_success_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, destination, direct_entry = _direct_manifest_objects()
    daily_manifest = build_archive_manifest(
        FakeBucket("daily", (_listed("data/fae/2026-04-13T00-00-00Z.txt", 1),)),
        run_started_at_utc=datetime(2026, 4, 27, 12, tzinfo=UTC),
        versioning_state="Enabled",
        parser_kind="filename_timestamp",
        copy_mode="daily_tar_gz",
        destination=destination,
        route_name="daily",
    )
    manifest = ArchiveManifest(
        datetime(2026, 4, 27, 12, tzinfo=UTC),
        (direct_entry, *daily_manifest.entries),
        None,
        daily_manifest.archive_groups,
        (),
    )

    def skip_direct(
        _route: ArchiveRoute,
        _entry: ManifestEntry,
        _debug_logger: DebugLogger | None,
    ) -> tuple[str | None, bool]:
        return None, False

    def skip_group(
        _source: ArchiveBucket,
        _destination: ArchiveBucket,
        _group: ArchiveGroup,
        _debug_logger: DebugLogger | None,
        **_kwargs: object,
    ) -> tuple[str | None, bool]:
        return None, False

    monkeypatch.setattr(archive_copy_module, "copy_direct_entry", skip_direct)
    monkeypatch.setattr(archive_copy_module, "copy_group", skip_group)

    phase, groups, entries = copy_phase(
        manifest,
        {
            "default": ArchiveRoute(
                "default",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
            ),
            "daily": ArchiveRoute(
                "daily",
                source,
                destination,
                parser_kind="filename_timestamp",
                copy_mode="daily_tar_gz",
            ),
        },
        None,
        lambda: False,
        lambda: 1.0,
    )

    assert phase.ok is True
    assert groups == ()
    assert entries == ()


@pytest.mark.unit()
def test_verify_phase_reports_direct_entry_verification_failure() -> None:
    source, destination, entry = _direct_manifest_objects()

    result = _verify_route_phase(
        (),
        (entry,),
        {
            "default": ArchiveRoute(
                "default",
                source,
                destination,
                parser_kind="direct",
                copy_mode="direct",
            )
        },
        lambda: False,
        lambda: 1.0,
    )

    assert result.failures == ("data/raw.txt: destination missing",)


def _direct_manifest_objects() -> tuple[FakeBucket, FakeBucket, ManifestEntry]:
    listed = _listed("data/raw.txt", 1, "v1")
    source = FakeBucket("source", (listed,))
    destination = FakeBucket("archive")
    manifest = build_archive_manifest(
        source,
        run_started_at_utc=datetime(2026, 4, 27, 12, tzinfo=UTC),
        versioning_state="Enabled",
        destination=destination,
        parser_kind="direct",
        copy_mode="direct",
    )
    return source, destination, manifest.entries[0]


def _verify_route_phase(
    groups: tuple[ArchiveGroup, ...],
    entries: tuple[ManifestEntry, ...],
    routes: dict[str, ArchiveRoute],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> ArchivePhaseResult:
    return verify_phase(groups, entries, routes, timed_out, time_remaining)


def _run_parallel_items() -> Callable[
    [
        tuple[str, ...],
        Callable[[str], tuple[str, ...]],
        Callable[[], bool],
        Callable[[], float],
    ],
    tuple[str, ...],
]:
    return run_parallel_items
