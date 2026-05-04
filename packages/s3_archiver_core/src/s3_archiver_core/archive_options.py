"""Archive option parsing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta

from s3_archiver_core._removed_env import reject_removed_archiver_env
from s3_archiver_core.archive_lock import parse_duration
from s3_archiver_core.archive_manifest import CopyMode, ParserKind
from s3_archiver_core.errors import ConfigError
from s3_archiver_core.s3 import S3TransferCapabilities, transfer_capabilities_for_locations
from s3_archiver_core.settings import AppSettings, RouteSettings


@dataclass(frozen=True, slots=True)
class ArchiveRouteOptions:
    """Route-specific archive workflow settings."""

    name: str
    source_path: str = ""
    destination_path: str = ""
    parser_kind: ParserKind = "filename_timestamp"
    copy_mode: CopyMode = "daily_tar_gz"


@dataclass(frozen=True, slots=True)
class ArchiveOptions:
    """Archive workflow settings with conservative defaults."""

    run_timeout: timedelta = timedelta(days=7)
    transfer_capabilities: S3TransferCapabilities = field(default_factory=S3TransferCapabilities)
    routes: tuple[ArchiveRouteOptions, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ArchiveOptions:
        """Build archive options from archive-specific environment variables."""

        reject_removed_archiver_env(env)
        return cls(
            run_timeout=_duration(env, "ARCHIVER_RUN_TIMEOUT", "7d"),
        )

    @classmethod
    def from_settings(cls, settings: AppSettings) -> ArchiveOptions:
        """Build archive options from validated application settings."""

        routes = _routes(settings)
        return cls(
            run_timeout=settings.run_timeout,
            transfer_capabilities=_transfer_capabilities(settings),
            routes=routes,
        )


def _transfer_capabilities(settings: AppSettings) -> S3TransferCapabilities:
    return transfer_capabilities_for_locations(settings.source, settings.destination)


def _routes(settings: AppSettings) -> tuple[ArchiveRouteOptions, ...]:
    return tuple(_route_options(route) for route in settings.routes)


def _route_options(route: RouteSettings) -> ArchiveRouteOptions:
    return ArchiveRouteOptions(
        name=route.name,
        source_path=route.source.path,
        destination_path=route.destination.path,
        parser_kind=route.parser.value,
        copy_mode=route.copy_mode.value,
    )


def _duration(env: Mapping[str, str], key: str, default: str) -> timedelta:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        raw = default
    try:
        return parse_duration(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a positive duration such as 7d") from exc
