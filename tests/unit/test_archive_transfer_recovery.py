"""Focused recovery-path tests for archive transfer fingerprints."""

from __future__ import annotations

from dataclasses import replace

import pytest
from s3_archiver_core.archive_fingerprint import (
    FINGERPRINT_METADATA_KEY,
    fingerprint_from_metadata,
    fingerprint_matches_entry,
)
from s3_archiver_core.archive_manifest import ManifestEntry
from s3_archiver_core.archive_transfer import (
    archive_metadata,
    recover_archived_entry,
    recover_fingerprinted_entry,
)
from s3_archiver_core.s3 import S3ObjectProperties

from tests.unit.archive_workflow_fakes import listed_object as _listed
from tests.unit.archive_workflow_fakes import object_properties as _properties


def _entry(*, source_bucket: str = "source", key: str = "old.txt") -> ManifestEntry:
    listed = _listed(key, 90, "v1")
    return ManifestEntry(source_bucket, key, 10, listed.last_modified, '"etag"', "v1", listed)


@pytest.mark.unit()
def test_recover_archived_entry_returns_original_for_other_source_identity() -> None:
    entry = _entry()
    archived = _entry(source_bucket="other-source")
    destination = replace(entry.object.properties, metadata=archive_metadata(archived))

    recovered = recover_archived_entry(entry, destination, lambda _version_id: _properties())

    assert recovered is entry


@pytest.mark.unit()
def test_recover_archived_entry_returns_original_when_archived_version_is_missing() -> None:
    entry = _entry()
    destination = replace(entry.object.properties, metadata=archive_metadata(entry))

    recovered = recover_archived_entry(entry, destination, lambda _version_id: None)

    assert recovered is entry


@pytest.mark.unit()
def test_recover_archived_entry_returns_original_for_invalid_timestamp_metadata() -> None:
    entry = _entry()
    invalid_metadata = dict(archive_metadata(entry))
    invalid_metadata["s3-archiver-source-fingerprint"] = (
        '{"source_bucket":"source","source_etag":"\\"etag\\"","source_key":"old.txt",'
        '"source_last_modified":"not-a-timestamp","source_size":10,"source_version_id":"v1"}'
    )
    destination = replace(entry.object.properties, metadata=invalid_metadata)

    recovered = recover_archived_entry(entry, destination, _properties_for_version)

    assert recovered is entry


@pytest.mark.unit()
def test_fingerprint_from_metadata_ignores_invalid_json_and_non_mapping_values() -> None:
    assert fingerprint_from_metadata({FINGERPRINT_METADATA_KEY: "not-json"}) is None
    assert fingerprint_from_metadata({FINGERPRINT_METADATA_KEY: "[]"}) is None


@pytest.mark.unit()
def test_fingerprint_from_metadata_ignores_malformed_mapping() -> None:
    assert fingerprint_from_metadata({FINGERPRINT_METADATA_KEY: '{"source_bucket":3}'}) is None


@pytest.mark.unit()
def test_fingerprint_from_metadata_defaults_non_mapping_checksums() -> None:
    fingerprint = fingerprint_from_metadata(
        {
            FINGERPRINT_METADATA_KEY: (
                '{"source_bucket":"bucket","source_key":"key","source_size":1,'
                '"source_last_modified":"time","source_checksums":"sha256"}'
            )
        }
    )

    assert fingerprint is not None
    assert fingerprint.source_checksums == {}


@pytest.mark.unit()
def test_fingerprint_from_metadata_coerces_checksum_mapping_values() -> None:
    fingerprint = fingerprint_from_metadata(
        {
            FINGERPRINT_METADATA_KEY: (
                '{"source_bucket":"bucket","source_key":"key","source_size":1,'
                '"source_last_modified":"time","source_checksums":{"sha256":123,"crc32":null}}'
            )
        }
    )

    assert fingerprint is not None
    assert fingerprint.source_checksums == {"sha256": "123"}


@pytest.mark.unit()
def test_fingerprint_matches_entry_rejects_checksum_mismatch() -> None:
    listed = _listed("old.txt", 90, "v1")
    entry = ManifestEntry(
        "source",
        "old.txt",
        10,
        listed.last_modified,
        '"etag"',
        "v1",
        replace(
            listed,
            properties=_properties(checksums={"sha256": "expected"}, checksum_type="FULL_OBJECT"),
        ),
    )
    fingerprint = fingerprint_from_metadata(archive_metadata(entry))

    assert fingerprint is not None
    mismatched_entry = ManifestEntry(
        "source",
        "old.txt",
        10,
        listed.last_modified,
        '"etag"',
        "v1",
        replace(
            listed,
            properties=_properties(checksums={"sha256": "other"}, checksum_type="FULL_OBJECT"),
        ),
    )

    assert fingerprint_matches_entry(fingerprint, mismatched_entry) is False


@pytest.mark.unit()
def test_recover_fingerprinted_entry_rejects_versioned_property_mismatch() -> None:
    listed = replace(
        _listed("old.txt", 90, "v1"),
        properties=_properties(checksums={"sha256": "expected"}, checksum_type="FULL_OBJECT"),
    )
    entry = ManifestEntry("source", "old.txt", 10, listed.last_modified, '"etag"', "v1", listed)
    destination = replace(entry.object.properties, metadata=archive_metadata(entry))

    recovered = recover_fingerprinted_entry(
        entry,
        destination,
        lambda _version_id: _properties(checksums={"sha256": "other"}, checksum_type="FULL_OBJECT"),
    )

    assert recovered is None


@pytest.mark.unit()
def test_recover_fingerprinted_entry_rejects_versioned_size_mismatch() -> None:
    entry = _entry()
    destination = replace(entry.object.properties, metadata=archive_metadata(entry))

    recovered = recover_fingerprinted_entry(
        entry,
        destination,
        lambda _version_id: _properties(size=11),
    )

    assert recovered is None


def _properties_for_version(_version_id: str | None) -> S3ObjectProperties:
    return _properties()
