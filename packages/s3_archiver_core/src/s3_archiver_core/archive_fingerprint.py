"""Portable archive fingerprint persistence and rerun recovery."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from s3_archiver_core._archive_identity import stable_identity_value
from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.s3 import S3ListedObject, S3ObjectProperties

FINGERPRINT_METADATA_KEY = "s3-archiver-source-fingerprint"


@dataclass(frozen=True, slots=True)
class SourceFingerprint:
    """Portable source identity persisted in destination metadata."""

    source_bucket: str
    source_identity: object | None
    source_key: str
    source_size: int
    source_last_modified: str
    source_version_id: str | None
    source_etag: str | None
    source_checksums: Mapping[str, str]
    source_checksum_type: str | None

    def to_metadata_value(self) -> str:
        """Serialize the fingerprint into stable JSON for S3 user metadata."""

        return json.dumps(
            {
                "source_bucket": self.source_bucket,
                "source_identity": self.source_identity,
                "source_key": self.source_key,
                "source_size": self.source_size,
                "source_last_modified": self.source_last_modified,
                "source_version_id": self.source_version_id,
                "source_etag": self.source_etag,
                "source_checksums": dict(self.source_checksums),
                "source_checksum_type": self.source_checksum_type,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def source_fingerprint(entry: ManifestEntry) -> SourceFingerprint:
    """Build the portable source fingerprint for one manifest entry."""

    return SourceFingerprint(
        source_bucket=entry.source_bucket,
        source_identity=stable_identity_value(entry.source_identity),
        source_key=entry.key,
        source_size=entry.size,
        source_last_modified=iso_timestamp(entry.last_modified),
        source_version_id=entry.version_id,
        source_etag=entry.etag,
        source_checksums=dict(entry.object.properties.checksums),
        source_checksum_type=entry.object.properties.checksum_type,
    )


def archive_metadata(entry: ManifestEntry) -> Mapping[str, str]:
    """Return destination metadata preserving source metadata plus fingerprint."""

    metadata = dict(entry.object.properties.metadata)
    if FINGERPRINT_METADATA_KEY in metadata:
        raise ValueError(f"source metadata uses reserved key {FINGERPRINT_METADATA_KEY}")
    metadata[FINGERPRINT_METADATA_KEY] = source_fingerprint(entry).to_metadata_value()
    return metadata


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


def recover_archived_entry(
    entry: ManifestEntry,
    destination: S3ObjectProperties,
    source_properties: Callable[[str | None], S3ObjectProperties | None],
) -> ManifestEntry:
    """Recover a prior archived source version from destination fingerprint metadata."""

    recovered = recover_fingerprinted_entry(
        entry,
        destination,
        source_properties,
        require_current_source_match=True,
    )
    return entry if recovered is None else recovered


def recover_fingerprinted_entry(
    entry: ManifestEntry,
    destination: S3ObjectProperties,
    source_properties: Callable[[str | None], S3ObjectProperties | None],
    *,
    require_current_source_match: bool = False,
) -> ManifestEntry | None:
    """Recover the source version pinned in destination fingerprint metadata."""

    fingerprint = fingerprint_from_metadata(destination.metadata)
    if fingerprint is None:
        return None
    if (
        fingerprint.source_bucket != entry.source_bucket
        or fingerprint.source_identity != stable_identity_value(entry.source_identity)
        or fingerprint.source_key != entry.key
    ):
        return None
    try:
        last_modified = datetime.fromisoformat(fingerprint.source_last_modified)
    except ValueError:
        return None
    properties = _recover_source_properties(
        fingerprint,
        source_properties,
        entry.object.properties,
        last_modified,
        require_current_source_match,
    )
    if properties is None:
        return None
    listed = S3ListedObject(
        entry.key,
        fingerprint.source_size,
        last_modified,
        fingerprint.source_etag,
        fingerprint.source_version_id,
        properties,
    )
    return replace(
        entry,
        size=fingerprint.source_size,
        last_modified=last_modified,
        etag=fingerprint.source_etag,
        version_id=fingerprint.source_version_id,
        object=listed,
    )


def fingerprint_matches_entry(fingerprint: SourceFingerprint, entry: ManifestEntry) -> bool:
    """Return whether a recovered fingerprint still matches the manifest entry."""

    if (
        fingerprint.source_bucket != entry.source_bucket
        or fingerprint.source_identity != stable_identity_value(entry.source_identity)
        or fingerprint.source_key != entry.key
        or fingerprint.source_size != entry.size
        or fingerprint.source_last_modified != iso_timestamp(entry.last_modified)
        or fingerprint.source_version_id != entry.version_id
        or fingerprint.source_etag != entry.etag
    ):
        return False
    if not checksums_consistent(fingerprint.source_checksums, entry.object.properties.checksums):
        return False
    return not (
        fingerprint.source_checksum_type is not None
        and entry.object.properties.checksum_type is not None
        and fingerprint.source_checksum_type != entry.object.properties.checksum_type
    )


def checksums_consistent(expected: Mapping[str, str], observed: Mapping[str, str]) -> bool:
    """Allow missing later checksum fields but reject conflicting overlaps."""

    return all(
        observed.get(algorithm, checksum) == checksum for algorithm, checksum in expected.items()
    )


def iso_timestamp(value: datetime) -> str:
    """Render a stable ISO-8601 timestamp for fingerprint persistence."""

    return value.isoformat()


def _recover_source_properties(
    fingerprint: SourceFingerprint,
    source_properties: Callable[[str | None], S3ObjectProperties | None],
    fallback_properties: S3ObjectProperties,
    last_modified: datetime,
    require_current_source_match: bool,
) -> S3ObjectProperties | None:
    if fingerprint.source_version_id is None:
        properties = source_properties(None) or fallback_properties
        if require_current_source_match and not _properties_match_fingerprint(
            fingerprint,
            properties,
            last_modified,
        ):
            return None
        return properties
    properties = source_properties(fingerprint.source_version_id)
    if properties is None:
        return None
    if not _properties_match_fingerprint(fingerprint, properties, last_modified):
        return None
    return properties


def _properties_match_fingerprint(
    fingerprint: SourceFingerprint,
    properties: S3ObjectProperties,
    last_modified: datetime,
) -> bool:
    if properties.last_modified is not None and properties.last_modified != last_modified:
        return False
    if properties.size != fingerprint.source_size or properties.etag != fingerprint.source_etag:
        return False
    if not checksums_consistent(fingerprint.source_checksums, properties.checksums):
        return False
    return not (
        fingerprint.source_checksum_type is not None
        and properties.checksum_type is not None
        and fingerprint.source_checksum_type != properties.checksum_type
    )


def _fingerprint_from_mapping(value: Mapping[str, object]) -> SourceFingerprint | None:
    bucket = _string_field(value, "source_bucket")
    key = _string_field(value, "source_key")
    last_modified = _string_field(value, "source_last_modified")
    size = value.get("source_size")
    if bucket is None or key is None or last_modified is None or not isinstance(size, int):
        return None
    return SourceFingerprint(
        source_bucket=bucket,
        source_identity=value.get("source_identity"),
        source_key=key,
        source_size=size,
        source_last_modified=last_modified,
        source_version_id=_optional_string_field(value, "source_version_id"),
        source_etag=_optional_string_field(value, "source_etag"),
        source_checksums=_string_mapping_field(value, "source_checksums"),
        source_checksum_type=_optional_string_field(value, "source_checksum_type"),
    )


def _string_field(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    return item if isinstance(item, str) else None


def _optional_string_field(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    return item if item is None or isinstance(item, str) else None


def _string_mapping_field(value: Mapping[str, object], key: str) -> Mapping[str, str]:
    item = value.get(key)
    if not isinstance(item, dict):
        return {}
    raw = cast(Mapping[object, object], item)
    return {
        str(mapping_key): str(mapping_value)
        for mapping_key, mapping_value in raw.items()
        if mapping_key is not None and mapping_value is not None
    }
