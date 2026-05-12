"""Helpers for extracting JSON payloads from mixed command output."""

from __future__ import annotations

import json
from typing import cast


def json_objects(output: str) -> list[dict[str, object]]:
    """Return JSON objects emitted as object-shaped lines in output."""

    stripped_output = output.strip()
    if stripped_output:
        payload = json.loads(stripped_output)
        if isinstance(payload, dict):
            return [cast(dict[str, object], payload)]
        if not isinstance(payload, list):
            return []

    objects: list[dict[str, object]] = []
    for line in output.splitlines():
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            objects.append(cast(dict[str, object], payload))
    return objects


def last_json_object(output: str) -> dict[str, object]:
    """Return the final JSON object emitted as an object-shaped output line."""

    payloads = json_objects(output)
    if not payloads:
        raise ValueError("output did not contain a JSON object line")
    return payloads[-1]
