"""Transfer strategy selection and destination verification."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.s3 import S3ObjectProperties, S3TransferCapabilities

TransferStrategy = Literal[
    "simple_native_copy",
    "multipart_native_copy",
    "multipart_streaming",
    "temp_file_backed",
]

FINGERPRINT_METADATA_KEY = "s3-archiver-source-fingerprint"
DEFAULT_SIMPLE_COPY_LIMIT_BYTES = 5 * 1024 * 1024 * 1024
DEFAULT_STREAMING_LIMIT_BYTES = 50 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """Portable source identity persisted in destination metadata."""

    source_bucket: str
    source_key: str
    source_size: int
    source_last_modified: str
    source_version_id: str | None
    source_etag: str | None

    def to_metadata_value(self) -> str:
        """Serialize the fingerprint into stable JSON for S3 user metadata."""

        return json.dumps(
            {
                "source_bucket": self.source_bucket,
                "source_key": self.source_key,
                "source_size": self.source_size,
                "source_last_modified": self.source_last_modified,
                "source_version_id": self.source_version_id,
                "source_etag": self.source_etag,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Result of destination verification."""

    ok: bool
    detail: str


def source_fingerprint(entry: ManifestEntry) -> SourceFingerprint:
    """Build the portable source fingerprint for one manifest entry."""

    return SourceFingerprint(
        source_bucket=entry.source_bucket,
        source_key=entry.key,
        source_size=entry.size,
        source_last_modified=_iso(entry.last_modified),
        source_version_id=entry.version_id,
        source_etag=entry.etag,
    )


def archive_metadata(entry: ManifestEntry) -> Mapping[str, str]:
    """Return destination metadata preserving source metadata plus fingerprint."""

    metadata = dict(entry.object.properties.metadata)
    if FINGERPRINT_METADATA_KEY in metadata:
        raise ValueError(f"source metadata uses reserved key {FINGERPRINT_METADATA_KEY}")
    metadata[FINGERPRINT_METADATA_KEY] = source_fingerprint(entry).to_metadata_value()
    return metadata


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
    expected = source_fingerprint(entry)
    if fingerprint != expected:
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


def verify_source_unchanged(
    entry: ManifestEntry, current: S3ObjectProperties | None
) -> VerificationResult:
    """Verify a key-only cleanup target still matches the manifest source object."""

    if current is None:
        return VerificationResult(False, "source missing before cleanup")
    source_properties = entry.object.properties
    if current.size != entry.size:
        return VerificationResult(False, "source changed before cleanup")
    if current.etag != entry.etag:
        return VerificationResult(False, "source changed before cleanup")
    if not _headers_match(source_properties, current):
        return VerificationResult(False, "source changed before cleanup")
    if dict(source_properties.metadata) != dict(current.metadata):
        return VerificationResult(False, "source changed before cleanup")
    if dict(source_properties.tags) != dict(current.tags):
        return VerificationResult(False, "source changed before cleanup")
    return VerificationResult(True, "ok")


def fingerprint_from_metadata(metadata: Mapping[str, str]) -> SourceFingerprint | None:
    """Decode a source fingerprint from destination metadata."""

    value = metadata.get(FINGERPRINT_METADATA_KEY)
    if value is None:
        return None
    try:
        decoded = cast(object, json.loads(value))
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    return _fingerprint_from_mapping(cast(Mapping[str, object], decoded))


def _fingerprint_from_mapping(value: Mapping[str, object]) -> SourceFingerprint | None:
    bucket = _string_field(value, "source_bucket")
    key = _string_field(value, "source_key")
    last_modified = _string_field(value, "source_last_modified")
    size = value.get("source_size")
    if bucket is None or key is None or last_modified is None or not isinstance(size, int):
        return None
    return SourceFingerprint(
        source_bucket=bucket,
        source_key=key,
        source_size=size,
        source_last_modified=last_modified,
        source_version_id=_optional_string_field(value, "source_version_id"),
        source_etag=_optional_string_field(value, "source_etag"),
    )


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


def _string_field(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if isinstance(item, str):
        return item
    return None


def _optional_string_field(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None or isinstance(item, str):
        return item
    return None


def _iso(value: datetime) -> str:
    return value.isoformat()
