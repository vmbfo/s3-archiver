"""Streaming archive verification and safe local restoration."""

from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
from _hashlib import HASH
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import BinaryIO, cast, final

from s3_archiver_core._archive_protocols import ArchiveBucket, ArchiveReadableBody
from s3_archiver_core.archive_group_metadata import ARCHIVE_SHA256_METADATA_KEY
from s3_archiver_core.archive_tar import ORIGINAL_KEY_PAX_HEADER, clear_member_cache
from s3_archiver_core.s3 import S3_CHUNK_BYTES

NO_SELECTION: frozenset[str] = frozenset()


@final
class _DigestReader:
    def __init__(self, body: ArchiveReadableBody) -> None:
        self.body: ArchiveReadableBody = body
        self.digest: HASH = hashlib.sha256()
        self.size: int = 0

    def read(self, amt: int = -1) -> bytes:
        data = self.body.read(amt)
        self.digest.update(data)
        self.size += len(data)
        return data


def inspect_archive(
    bucket: ArchiveBucket,
    key: str,
    *,
    report_member: Callable[[str, str, int], None] | None = None,
    output_dir: Path | None = None,
    selected: frozenset[str] = NO_SELECTION,
) -> tuple[int, str, int]:
    """Read once, verify compressed SHA-256/size, optionally list or extract.

    Restored files become visible only after the entire archive verifies. The
    output directory must not exist. Original unsafe keys remain encoded using
    the tar's safe member name. No links or special files are restored.
    """
    properties = bucket.head_object(key)
    if properties is None:
        raise FileNotFoundError(key)
    expected = properties.metadata.get(ARCHIVE_SHA256_METADATA_KEY)
    if not expected:
        raise ValueError(f"{key}: missing archive SHA-256")
    if not properties.etag:
        raise ValueError(f"{key}: missing archive ETag for consistent read")
    if output_dir is not None:
        if output_dir.exists() or output_dir.is_symlink():
            raise FileExistsError(output_dir)
        output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="s3-archiver-restore-", dir=output_dir.parent if output_dir else bucket.temp_dir
    ) as temp:
        staging = Path(temp) / "files"
        staging.mkdir()
        body = bucket.read_source_stream(key, if_match=properties.etag)
        reader = _DigestReader(body)
        try:
            count, found = _read_members(
                reader, staging if output_dir else None, selected, report_member
            )
            # tar ends at its zero blocks; include the gzip trailer and any remaining bytes.
            while reader.read(S3_CHUNK_BYTES):
                pass
        finally:
            body.close()
        actual = reader.digest.hexdigest()
        if reader.size != properties.size or actual != expected:
            raise ValueError(f"{key}: archive content verification failed")
        if selected - found:
            raise ValueError(f"{key}: requested members missing: {sorted(selected - found)}")
        if output_dir is not None:
            # Recheck to avoid replacing an output created during a long download.
            if output_dir.exists() or output_dir.is_symlink():
                raise FileExistsError(output_dir)
            _ = staging.rename(output_dir)
    return count, actual, reader.size


def _read_members(
    reader: _DigestReader,
    staging: Path | None,
    selected: frozenset[str],
    report_member: Callable[[str, str, int], None] | None,
) -> tuple[int, set[str]]:
    count = 0
    found: set[str] = set()
    with tarfile.open(
        fileobj=cast(BinaryIO, cast(object, reader)), mode="r|gz", bufsize=S3_CHUNK_BYTES
    ) as tar:
        for member in tar:
            original = member.pax_headers.get(ORIGINAL_KEY_PAX_HEADER, member.name)
            if not member.isfile():
                raise ValueError(f"{member.name}: unsupported archive member type")
            if report_member is not None:
                report_member(original, member.name, member.size)
            count += 1
            if original in selected:
                found.add(original)
            if staging is not None and (not selected or original in selected):
                target = _safe_target(staging, member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                assert source is not None
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=S3_CHUNK_BYTES)
            clear_member_cache(tar)
    return count, found


def _safe_target(staging: Path, name: str) -> Path:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or (len(name) >= 2 and name[1] == ":")
    ):
        raise ValueError(f"{name}: unsafe archive member name")
    return staging.joinpath(*path.parts)
