"""Archive expectation helpers for the manual visual demo."""

from __future__ import annotations

import hashlib

from s3_archiver_core.archive_tar import ORIGINAL_KEY_PAX_HEADER


def archive_member_name(key: str) -> str:
    """Return the tar member name expected for a source object key."""

    if key.startswith(("C:", "s3-archiver-safe/")):
        return f"s3-archiver-safe/{hashlib.sha256(key.encode()).hexdigest()}"
    return key


def sampled_archive_members(archive_members: dict[str, set[str]]) -> dict[str, set[str]]:
    """Return a first, middle, and last archive sample for verification."""

    keys = sorted(archive_members)
    return {key: archive_members[key] for key in (keys[0], keys[len(keys) // 2], keys[-1])}


def expected_pax_headers(source_keys: set[str]) -> dict[str, dict[str, str]]:
    """Return expected PAX headers for unsafe tar member names."""

    return {
        archive_member_name(key): {ORIGINAL_KEY_PAX_HEADER: key}
        for key in source_keys
        if archive_member_name(key) != key
    }
