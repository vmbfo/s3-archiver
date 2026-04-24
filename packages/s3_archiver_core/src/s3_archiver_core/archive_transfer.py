"""Transfer strategy selection and destination verification."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from s3_archiver_core.archive_fingerprint import (
    FINGERPRINT_METADATA_KEY,
    archive_metadata,
    fingerprint_from_metadata,
    recover_archived_entry,
    recover_fingerprinted_entry,
)
from s3_archiver_core.archive_fingerprint import (
    fingerprint_matches_entry as _fingerprint_matches_entry,
)
from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.s3 import S3ObjectProperties, S3TransferCapabilities

TransferStrategy = Literal[
    "simple_native_copy",
    "multipart_native_copy",
    "multipart_streaming",
    "temp_file_backed",
]

DEFAULT_SIMPLE_COPY_LIMIT_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_STREAMING_LIMIT_BYTES = 50 * 1024 * 1024 * 1024

__all__ = (
    "FINGERPRINT_METADATA_KEY",
    "VerificationResult",
    "archive_metadata",
    "fingerprint_from_metadata",
    "recover_archived_entry",
    "recover_fingerprinted_entry",
    "select_transfer_strategy",
    "verify_destination",
    "verify_destination_checksum",
    "verify_destination_content",
    "verify_source_unchanged",
)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Result of destination verification."""

    ok: bool
    detail: str


def select_transfer_strategy(
    size: int,
    capabilities: S3TransferCapabilities,
    *,
    simple_copy_limit_bytes: int = DEFAULT_SIMPLE_COPY_LIMIT_BYTES,
    streaming_limit_bytes: int = DEFAULT_STREAMING_LIMIT_BYTES,
) -> TransferStrategy:
    """Choose the transfer strategy for a source object."""

    if capabilities.native_copy and size <= simple_copy_limit_bytes:
        return "simple_native_copy"
    if capabilities.native_copy and capabilities.multipart_copy:
        return "multipart_native_copy"
    if capabilities.streaming_upload and size <= streaming_limit_bytes:
        return "multipart_streaming"
    return "temp_file_backed"


def verify_destination(
    entry: ManifestEntry, destination: S3ObjectProperties | None
) -> VerificationResult:
    """Verify a destination object is the archived copy of the manifest source."""

    if destination is None:
        return VerificationResult(False, "destination missing")
    fingerprint = fingerprint_from_metadata(destination.metadata)
    if fingerprint is None:
        return VerificationResult(False, "source fingerprint mismatch")
    if not _fingerprint_matches_entry(fingerprint, entry):
        return VerificationResult(False, "source fingerprint mismatch")
    if destination.size != entry.size:
        return VerificationResult(False, "size mismatch")
    source_properties = entry.object.properties
    if not _headers_match(source_properties, destination):
        return VerificationResult(False, "object property mismatch")
    if not _metadata_match(source_properties.metadata, destination.metadata):
        return VerificationResult(False, "metadata mismatch")
    if dict(source_properties.tags) != dict(destination.tags):
        return VerificationResult(False, "tag mismatch")
    return VerificationResult(True, "ok")


def verify_destination_content(
    source_sha256: str | None, destination_sha256: str | None
) -> VerificationResult:
    """Verify the destination payload matches the source payload."""

    if source_sha256 is None:
        return VerificationResult(False, "source missing during verification")
    if destination_sha256 is None:
        return VerificationResult(False, "destination missing")
    if source_sha256 != destination_sha256:
        return VerificationResult(False, "content mismatch")
    return VerificationResult(True, "ok")


def verify_destination_checksum(
    source: S3ObjectProperties, destination: S3ObjectProperties
) -> VerificationResult | None:
    """Verify payload integrity from shared object checksums when available."""

    shared_algorithms = [
        algorithm
        for algorithm in ("sha256", "sha1", "crc64nvme", "crc32c", "crc32")
        if algorithm in source.checksums and algorithm in destination.checksums
    ]
    if not shared_algorithms:
        return None
    if source.checksum_type != "FULL_OBJECT" or destination.checksum_type != "FULL_OBJECT":
        return None
    for algorithm in shared_algorithms:
        if source.checksums[algorithm] != destination.checksums[algorithm]:
            return VerificationResult(False, "content mismatch")
    return VerificationResult(True, "ok")


def verify_source_unchanged(
    entry: ManifestEntry, current: S3ObjectProperties | None
) -> VerificationResult:
    """Verify a key-only cleanup target still matches the manifest source object."""

    if current is None:
        return VerificationResult(False, "source missing before cleanup")
    source_properties = entry.object.properties
    if current.last_modified is not None and current.last_modified != entry.last_modified:
        return VerificationResult(False, "source changed before cleanup")
    if current.size != entry.size:
        return VerificationResult(False, "source changed before cleanup")
    if current.etag != entry.etag:
        return VerificationResult(False, "source changed before cleanup")
    checksum_verified = verify_destination_checksum(source_properties, current)
    if checksum_verified is not None and not checksum_verified.ok:
        return VerificationResult(False, "source changed before cleanup")
    if not _headers_match(source_properties, current):
        return VerificationResult(False, "source changed before cleanup")
    if dict(source_properties.metadata) != dict(current.metadata):
        return VerificationResult(False, "source changed before cleanup")
    if dict(source_properties.tags) != dict(current.tags):
        return VerificationResult(False, "source changed before cleanup")
    return VerificationResult(True, "ok")


def _headers_match(source: S3ObjectProperties, destination: S3ObjectProperties) -> bool:
    return (
        source.content_type == destination.content_type
        and source.content_encoding == destination.content_encoding
        and source.content_language == destination.content_language
        and source.content_disposition == destination.content_disposition
        and source.cache_control == destination.cache_control
        and source.expires == destination.expires
    )


def _metadata_match(source: Mapping[str, str], destination: Mapping[str, str]) -> bool:
    cleaned_destination = {
        key: value for key, value in destination.items() if key != FINGERPRINT_METADATA_KEY
    }
    return dict(source) == cleaned_destination
