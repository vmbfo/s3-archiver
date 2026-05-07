from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from s3_archiver_core._archive_copy import copy_group as _copy_group_impl
from s3_archiver_core._archive_copy import copy_phase as _copy_phase_impl
from s3_archiver_core._archive_copy import verify_phase as _verify_phase_impl
from s3_archiver_core._archive_protocols import ArchiveBucket, ArchiveRunLock
from s3_archiver_core._archive_routes import ArchiveRoute, DebugLogger
from s3_archiver_core.archive_group_metadata import (
    ARCHIVE_SHA256_METADATA_KEY,
    MANIFEST_SHA256_METADATA_KEY,
    group_metadata,
)
from s3_archiver_core.archive_manifest import (
    ArchiveGroup,
    ArchiveManifest,
    ArchiveManifestRoute,
    ManifestEntry,
    build_route_archive_manifest,
)
from s3_archiver_core.archive_options import ArchiveOptions
from s3_archiver_core.archive_result import ArchivePhaseResult, ArchiveRunResult

__all__ = (
    "ARCHIVE_SHA256_METADATA_KEY",
    "MANIFEST_SHA256_METADATA_KEY",
    "ArchivePhaseResult",
    "ArchiveRoute",
    "ArchiveRunResult",
    "group_metadata",
    "run_archive",
    "run_archive_routes",
)


def run_archive(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    options: ArchiveOptions,
    *,
    run_started_at_utc: datetime | None = None,
    run_lock: ArchiveRunLock | None = None,
    debug_logger: DebugLogger | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ArchiveRunResult:
    """Run one archive pass from source objects into destination archives."""

    if not options.routes:
        raise ValueError("archive options must include at least one route")
    route_option = options.routes[0]
    route = ArchiveRoute(
        name=route_option.name,
        source=source,
        destination=destination,
        parser_kind=route_option.parser_kind,
        copy_mode=route_option.copy_mode,
        source_path=route_option.source_path,
        destination_path=route_option.destination_path,
        transfer_capabilities=options.transfer_capabilities,
    )
    return run_archive_routes(
        (route,),
        options,
        run_started_at_utc=run_started_at_utc,
        run_lock=run_lock,
        debug_logger=debug_logger,
        clock=clock,
    )


def run_archive_routes(
    routes: tuple[ArchiveRoute, ...],
    options: ArchiveOptions,
    *,
    run_started_at_utc: datetime | None = None,
    run_lock: ArchiveRunLock | None = None,
    debug_logger: DebugLogger | None = None,
    clock: Callable[[], datetime] | None = None,
) -> ArchiveRunResult:
    """Run one archive pass with one execution worker per route."""

    now = clock or (lambda: datetime.now(tz=UTC))
    started = _as_utc(run_started_at_utc or now())
    deadline = started + options.run_timeout
    run_id = uuid4().hex
    if run_lock is not None and not run_lock.acquire(
        run_id=run_id, run_started_at_utc=started, timeout=options.run_timeout
    ):
        raise RuntimeError("archive run lock is already held")
    try:
        try:
            manifest = _build_manifest(routes, started)
        except Exception as exc:
            return ArchiveRunResult(
                run_id,
                _empty_manifest(started),
                _skipped("copy"),
                _skipped("verify"),
                ArchivePhaseResult("list", (str(exc),)),
            )
        if _timed_out(now, deadline):
            return _run_result(run_id, manifest, _timeout("copy"), _skipped("verify"))

        def timed_out() -> bool:
            return _timed_out(now, deadline)

        def time_remaining() -> float:
            return max((deadline - now()).total_seconds(), 0.0)

        routes_by_name = {route.name: route for route in routes}
        copy_result, verified_groups, verified_entries = _copy_phase_impl(
            manifest,
            routes_by_name,
            debug_logger,
            timed_out,
            time_remaining,
        )
        if _timed_out(now, deadline):
            return _run_result(run_id, manifest, _timeout("copy"), _skipped("verify"))
        verify_result = (
            _skipped("verify")
            if not copy_result.ok
            else _verify_phase_impl(
                verified_groups,
                verified_entries,
                routes_by_name,
                timed_out,
                time_remaining,
            )
        )
        if copy_result.ok and _timed_out(now, deadline):
            return _run_result(run_id, manifest, copy_result, _timeout("verify"))
        return ArchiveRunResult(
            run_id,
            manifest,
            copy_result,
            verify_result,
        )
    finally:
        if run_lock is not None:
            run_lock.release(run_id=run_id)


def _run_result(
    run_id: str,
    manifest: ArchiveManifest,
    copy: ArchivePhaseResult,
    verify: ArchivePhaseResult,
) -> ArchiveRunResult:
    return ArchiveRunResult(run_id, manifest, copy, verify)


def _copy_group(
    source: ArchiveBucket,
    destination: ArchiveBucket,
    group: ArchiveGroup,
    debug_logger: DebugLogger | None,
) -> tuple[str | None, bool]:
    return _copy_group_impl(source, destination, group, debug_logger)


def _verify_phase(
    groups: tuple[ArchiveGroup, ...],
    entries: tuple[ManifestEntry, ...],
    routes: dict[str, ArchiveRoute],
    timed_out: Callable[[], bool],
    time_remaining: Callable[[], float],
) -> ArchivePhaseResult:
    return _verify_phase_impl(
        groups,
        entries,
        routes,
        timed_out,
        time_remaining,
    )


_PRIVATE_TEST_HOOKS = (_copy_group, _verify_phase)


def _build_manifest(
    routes: tuple[ArchiveRoute, ...],
    started: datetime,
) -> ArchiveManifest:
    return build_route_archive_manifest(
        tuple(
            ArchiveManifestRoute(
                route.name,
                route.source,
                route.destination,
                parser_kind=route.parser_kind,
                copy_mode=route.copy_mode,
                source_path=route.source_path,
                destination_path=route.destination_path,
                source_identity=route.source_identity,
                destination_identity=route.destination_identity,
            )
            for route in routes
        ),
        run_started_at_utc=started,
    )


def _skipped(phase: str) -> ArchivePhaseResult:
    return ArchivePhaseResult(phase, skipped=True)


def _timed_out(clock: Callable[[], datetime], deadline: datetime) -> bool:
    return clock() > deadline


def _timeout(phase: str) -> ArchivePhaseResult:
    return ArchivePhaseResult(phase, ("archive run timed out",))


def _empty_manifest(started: datetime) -> ArchiveManifest:
    return ArchiveManifest(started, (), None, (), ())


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
