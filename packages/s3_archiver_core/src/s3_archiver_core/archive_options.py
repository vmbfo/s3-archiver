"""Archive option parsing."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import KW_ONLY, dataclass, field
from datetime import timedelta

from s3_archiver_core.archive_manifest import CopyMode, ParserKind
from s3_archiver_core.s3 import S3TransferCapabilities, transfer_capabilities_for_locations
from s3_archiver_core.settings import AppSettings, RouteSettings


@dataclass(frozen=True, slots=True)
class ArchiveRouteOptions:
    """Route-specific archive workflow settings."""

    name: str
    _: KW_ONLY
    parser_kind: ParserKind
    copy_mode: CopyMode
    source_path: str = ""
    destination_path: str = ""


@dataclass(frozen=True, slots=True)
class ArchiveOptions:
    """Archive workflow settings with conservative defaults."""

    run_timeout: timedelta = timedelta(days=7)
    transfer_capabilities: S3TransferCapabilities = field(default_factory=S3TransferCapabilities)
    routes: tuple[ArchiveRouteOptions, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ArchiveOptions:
        """Build archive options from validated application settings."""

        return cls.from_settings(AppSettings.from_env(env))

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
