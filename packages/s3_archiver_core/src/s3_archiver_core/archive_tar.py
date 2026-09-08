"""Deterministic tar.gz archive creation."""

from __future__ import annotations

import gzip
import hashlib
import tarfile
from _hashlib import HASH
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Protocol, cast, final

from s3_archiver_core._archive_object_activity import (
    entry_activity_watchdog,
    log_large_entry,
)
from s3_archiver_core._archive_protocols import ArchiveBucket
from s3_archiver_core.archive_manifest import ArchiveGroup
from s3_archiver_core.s3 import S3_CHUNK_BYTES

ORIGINAL_KEY_PAX_HEADER = "s3-archiver.original-key"
_SAFE_MEMBER_PREFIX = "s3-archiver-safe/"


class _MemberCache(Protocol):
    members: list[tarfile.TarInfo]


def clear_member_cache(tar: tarfile.TarFile) -> None:
    """Discard headers cached by Python 3.12 tarfile, including in streaming mode."""
    cast(_MemberCache, cast(object, tar)).members.clear()


def write_tar_gz_archive(
    source: ArchiveBucket,
    group: ArchiveGroup,
    path: Path,
    *,
    progress_logger: Callable[[], None] | None = None,
) -> str:
    """Write a deterministic tar.gz archive for one archive group."""

    digest = hashlib.sha256()
    with (
        path.open("wb") as raw,
        gzip.GzipFile(
            filename="", fileobj=_HashingWriter(raw, digest), mode="wb", mtime=0, compresslevel=1
        ) as gzip_file,
        tarfile.TarFile(fileobj=gzip_file, mode="w", copybufsize=S3_CHUNK_BYTES) as tar,
    ):
        for entry in group.entries:
            log_large_entry(
                operation="archive_member_write",
                entry=entry,
                destination_bucket=group.destination_bucket,
                destination_key=group.destination_archive_key,
            )
            with entry_activity_watchdog(
                operation="archive_member_write",
                entry=entry,
                destination_bucket=group.destination_bucket,
                destination_key=group.destination_archive_key,
            ):
                if entry.version_id in (None, "null") and not entry.etag:
                    raise ValueError(f"{entry.key}: unversioned source has no ETag")
                body = source.read_source_stream(entry.key, entry.version_id, if_match=entry.etag)
                try:
                    member_name, pax_headers = _member_name(entry.key)
                    info = tarfile.TarInfo(member_name)
                    info.size = entry.size
                    info.mtime = 0
                    info.mode = 0o644
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.pax_headers = dict(pax_headers)
                    tar.addfile(info, body)
                    clear_member_cache(tar)
                    if body.read(1):
                        raise ValueError(f"{entry.key}: source exceeds listed size")
                    if progress_logger is not None:
                        progress_logger()
                finally:
                    body.close()

    return digest.hexdigest()


@final
class _HashingWriter:
    def __init__(self, raw: BinaryIO, digest: HASH) -> None:
        self.raw = raw
        self.digest = digest

    def write(self, data: bytes) -> int:
        written = self.raw.write(data)
        self.digest.update(data[:written])
        return written

    def flush(self) -> None:
        self.raw.flush()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(S3_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _member_name(key: str) -> tuple[str, dict[str, str]]:
    if _safe_member_name(key):
        return key, {}

    digest = hashlib.sha256(key.encode()).hexdigest()
    return f"{_SAFE_MEMBER_PREFIX}{digest}", {ORIGINAL_KEY_PAX_HEADER: key}


def _safe_member_name(key: str) -> bool:
    return (
        key != ""
        and not key.startswith(_SAFE_MEMBER_PREFIX)
        and not key.startswith("/")
        and "\\" not in key
        and not _has_windows_drive(key)
        and ".." not in key.split("/")
    )


def _has_windows_drive(key: str) -> bool:
    return len(key) >= 2 and key[1] == ":" and key[0].isalpha()
