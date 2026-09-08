"""Operator commands for streaming archive audits and restoration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from s3_archiver_core.archive_inspection import NO_SELECTION, inspect_archive
from s3_archiver_core.archive_routes import archive_routes_from_settings
from s3_archiver_core.s3 import build_s3_client
from s3_archiver_core.settings import AppSettings

from s3_archiver_cli.env import load_runtime_env

app: typer.Typer = typer.Typer(
    help="List, verify, or restore an archive using a configured destination route."
)


@app.command("list")
def list_members(route: str, key: str) -> None:
    """Stream member records as JSON lines and verify the complete archive."""
    _run(route, key, list_members=True)


@app.command("verify")
def verify(route: str, key: str) -> None:
    """Read the full archive and check tar readability, stored size, and SHA-256."""
    _run(route, key)


@app.command("extract")
def extract(
    route: str,
    key: str,
    output_dir: Path,
    member: Annotated[
        list[str] | None, typer.Option(help="Original key; repeat to select members.")
    ] = None,
) -> None:
    """Restore into a new directory, publishing it only after full verification."""
    _run(route, key, output_dir=output_dir, members=frozenset(member or ()))


def _report(original: str, name: str, size: int) -> None:
    typer.echo(json.dumps({"key": original, "member": name, "size": size}, sort_keys=True))


def _run(
    route_name: str,
    key: str,
    *,
    list_members: bool = False,
    output_dir: Path | None = None,
    members: frozenset[str] = NO_SELECTION,
) -> None:
    try:
        settings = AppSettings.from_env(load_runtime_env())
        routes = archive_routes_from_settings(settings, build_s3_client)
        route = next((item for item in routes if item.name == route_name), None)
        if route is None:
            raise ValueError(f"unknown route: {route_name}")
        route.destination.temp_dir.mkdir(parents=True, exist_ok=True)
        count, digest, size = inspect_archive(
            route.destination,
            key,
            report_member=_report if list_members else None,
            output_dir=output_dir,
            selected=members,
        )
        typer.echo(json.dumps({"status": "ok", "members": count, "sha256": digest, "bytes": size}))
    except Exception as exc:
        typer.echo(json.dumps({"status": "error", "detail": str(exc)}), err=True)
        raise typer.Exit(code=1) from exc
