"""Small, content-addressed membership sidecars; never scan archive payloads to refresh."""

from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO, cast

from s3_archiver_core._archive_manifest_digest import manifest_entry_bytes
from s3_archiver_core._archive_manifest_models import ArchiveGroup
from s3_archiver_core._archive_protocols import ArchiveBucket

MEMBERSHIP_METADATA_KEY = "s3-archiver-membership-key"


def membership_key(archive_key: str, manifest_sha256: str) -> str:
    """Return the immutable sidecar key for one manifest digest."""
    return f"{archive_key}.members.{manifest_sha256}.jsonl.gz"


def upload_membership(destination: ArchiveBucket, group: ArchiveGroup, manifest_sha256: str) -> str:
    """Publish the immutable sidecar before publishing the archive referring to it."""
    key = membership_key(group.destination_archive_key, manifest_sha256)
    with tempfile.NamedTemporaryFile(dir=destination.temp_dir, suffix=".jsonl.gz") as temp:
        path = Path(temp.name)
        with (
            path.open("wb") as raw,
            gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=1) as output,
        ):
            for entry in group.entries:
                _ = output.write(manifest_entry_bytes(entry) + b"\n")
        destination.upload_archive_file(key, path, {})
        stored = destination.head_object(key)
        if stored is None or stored.size != path.stat().st_size:
            raise ValueError(f"{key}: membership upload size mismatch")
    return key


def contains_previous_membership(
    destination: ArchiveBucket, group: ArchiveGroup, existing: Mapping[str, str]
) -> bool:
    """Stream a sorted merge and authenticate all old identities against their digest.

    Memory is O(one member); reads are O(old + new members), not archive bytes.
    Missing legacy sidecars, changed versions, missing keys and malformed data fail closed.
    """
    old_digest = existing.get("s3-archiver-manifest-sha256", "")
    key = membership_key(group.destination_archive_key, old_digest)
    if existing.get(MEMBERSHIP_METADATA_KEY) != key:
        return False
    body = destination.read_source_stream(key)
    digest = hashlib.sha256(b"[")
    count = 0
    entries = iter(group.entries)
    candidate = next(entries, None)
    previous_key: str | None = None
    try:
        with gzip.GzipFile(fileobj=cast(BinaryIO, cast(object, body)), mode="rb") as source:
            for line in source:
                row = line.removesuffix(b"\n")
                decoded = cast(dict[str, object], json.loads(row))
                old_key = decoded.get("key")
                if not isinstance(old_key, str) or (
                    previous_key is not None and old_key <= previous_key
                ):
                    return False
                previous_key = old_key
                while candidate is not None and candidate.key < old_key:
                    candidate = next(entries, None)
                if candidate is None or manifest_entry_bytes(candidate) != row:
                    return False
                if count:
                    digest.update(b",")
                digest.update(row)
                count += 1
                candidate = next(entries, None)
    finally:
        body.close()
    digest.update(b"]")
    return digest.hexdigest() == old_digest and str(count) == existing.get(
        "s3-archiver-source-count"
    )
