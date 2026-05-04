"""Shared helpers for CLI archive unit tests."""

from __future__ import annotations

import json
from datetime import datetime
from typing import NotRequired, TypedDict, cast

from s3_archiver_core.archive import ArchivePhaseResult, ArchiveRunResult
from s3_archiver_core.archive_manifest import ArchiveManifest


class ArchivePayload(TypedDict):
    """Typed CLI archive payload."""

    status: str
    phase: NotRequired[str]
    key: NotRequired[str | None]
    message: NotRequired[str]
    details: NotRequired[str]
    source_bucket: NotRequired[str]
    destination_bucket: NotRequired[str]
    phases: NotRequired[dict[str, object]]


def load_archive_payload(output: str) -> ArchivePayload:
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = cast(object, json.loads(stripped))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return cast(ArchivePayload, cast(object, payload))
    raise AssertionError(f"expected JSON payload in output: {output!r}")


def archive_result(
    *,
    copy: ArchivePhaseResult | None = None,
    verify: ArchivePhaseResult | None = None,
) -> ArchiveRunResult:
    return ArchiveRunResult(
        run_id="run-id",
        manifest=ArchiveManifest(
            run_started_at_utc=datetime.fromisoformat("2026-04-09T17:00:43+00:00"),
            entries=(),
        ),
        copy=copy or ArchivePhaseResult("copy"),
        verify=verify or ArchivePhaseResult("verify"),
    )
