"""Private shared LocalStack support internals."""

from __future__ import annotations

from collections.abc import Collection
from typing import cast

RETRYABLE_LOCALSTACK_ERRORS = (
    "Connection was closed before we received a valid response",
    "Could not connect to the endpoint URL",
)

RETRYABLE_LOCALSTACK_COMPOSE_MESSAGES = (
    "No such container",
    "marked for removal",
    "HeadBucket operation: Not Found",
)


def object_entries(value: object) -> list[dict[str, object]]:
    """Return dictionary entries from an S3 list response section."""

    if not isinstance(value, list):
        return []
    entries = cast(list[object], value)
    return [cast(dict[str, object], entry) for entry in entries if isinstance(entry, dict)]


def is_retryable_localstack_message(
    message: str,
    extra_fragments: Collection[str] = (),
) -> bool:
    """Return whether text matches known LocalStack startup races."""

    fragments = (*RETRYABLE_LOCALSTACK_ERRORS, *extra_fragments)
    return any(fragment in message for fragment in fragments)


def is_retryable_localstack_error(exc: Exception) -> bool:
    """Return whether an exception text matches known LocalStack startup races."""

    return is_retryable_localstack_message(str(exc))
