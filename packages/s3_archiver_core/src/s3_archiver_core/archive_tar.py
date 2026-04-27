"""Deterministic tar.gz archive creation."""

from __future__ import annotations

import gzip
import hashlib
import tarfile
from pathlib import Path

from s3_archiver_core._archive_protocols import ArchiveBucket
from s3_archiver_core.archive_manifest import ArchiveGroup
from s3_archiver_core.s3 import S3_CHUNK_BYTES


def write_tar_gz_archive(source: ArchiveBucket, group: ArchiveGroup, path: Path) -> None:
    """Write a deterministic tar.gz archive for one archive group."""

    with (
        path.open("wb") as raw,
        gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gzip_file,
        tarfile.open(fileobj=gzip_file, mode="w") as tar,
    ):
        for entry in sorted(group.entries, key=lambda item: item.key):
            body = source.read_source_stream(entry.key, entry.version_id)
            try:
                info = tarfile.TarInfo(entry.key)
                info.size = entry.size
                info.mtime = 0
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                tar.addfile(info, body)
            finally:
                body.close()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file."""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(S3_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()
