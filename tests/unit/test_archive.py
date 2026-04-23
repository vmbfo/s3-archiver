"""Unit tests for archive workflow primitives."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from s3_archiver_core.archive import run_archive
from s3_archiver_core.archive_manifest import (
    ManifestEntry,
    SourcePathFilter,
    build_archive_manifest,
)
from s3_archiver_core.archive_options import ArchiveOptions, cleanup_enabled_from_env
from s3_archiver_core.archive_transfer import (
    FINGERPRINT_METADATA_KEY,
    TransferStrategy,
    archive_metadata,
    select_transfer_strategy,
    verify_destination,
)
from s3_archiver_core.s3 import (
    S3ListedObject,
    S3ObjectProperties,
    S3TransferCapabilities,
    VersioningState,
)


def _properties(
    *,
    size: int = 10,
    metadata: Mapping[str, str] | None = None,
    tags: Mapping[str, str] | None = None,
) -> S3ObjectProperties:
    return S3ObjectProperties(
        size=size,
        etag='"etag"',
        content_type="text/plain",
        content_encoding="gzip",
        content_language="en",
        content_disposition="inline",
        cache_control="max-age=60",
        expires=datetime(2024, 1, 1, tzinfo=UTC),
        metadata=metadata or {"owner": "archive"},
        tags=tags or {"kind": "source"},
    )


def _listed(key: str, age_days: int, version_id: str | None = "v1") -> S3ListedObject:
    size = 10
    return S3ListedObject(
        key=key,
        size=size,
        last_modified=datetime(2024, 4, 20, tzinfo=UTC) - timedelta(days=age_days),
        etag='"etag"',
        version_id=version_id,
        properties=_properties(size=size),
    )


class FakeBucket:
    """In-memory archive bucket test double."""

    bucket: str
    copied: list[str]
    deleted: list[tuple[str, str | None]]
    fail_copy: bool
    _objects: dict[str, S3ListedObject]
    _destination: dict[str, S3ObjectProperties]
    _versioning_state: VersioningState

    def __init__(
        self,
        bucket: str,
        objects: Iterable[S3ListedObject] = (),
        destination: Mapping[str, S3ObjectProperties] | None = None,
        versioning_state: VersioningState = "Enabled",
    ) -> None:
        self.bucket = bucket
        self.copied = []
        self.deleted = []
        self.fail_copy = False
        self._objects = {item.key: item for item in objects}
        self._destination = dict(destination or {})
        self._versioning_state = versioning_state

    def versioning_state(self) -> VersioningState:
        return self._versioning_state

    def list_source_objects(self, versioning_state: VersioningState) -> Iterable[S3ListedObject]:
        assert versioning_state == self._versioning_state
        return tuple(self._objects.values())

    def head_object(self, key: str, version_id: str | None = None) -> S3ObjectProperties | None:
        _ = version_id
        return self._destination.get(key)

    def copy_from(
        self,
        source: object,
        source_bucket: str,
        source_key: str,
        source_version_id: str | None,
        properties: S3ObjectProperties,
        destination_key: str,
        destination_metadata: Mapping[str, str],
        strategy: TransferStrategy,
    ) -> None:
        assert isinstance(source, FakeBucket)
        assert source.bucket == source_bucket
        _ = (source_version_id, strategy)
        if self.fail_copy:
            raise RuntimeError("copy failed")
        self.copied.append(source_key)
        self._destination[destination_key] = replace(properties, metadata=destination_metadata)

    def delete_source(self, key: str, version_id: str | None) -> None:
        self.deleted.append((key, version_id))


@pytest.mark.unit()
def test_manifest_uses_frozen_cutoff_filters_and_preserves_versions() -> None:
    source = FakeBucket(
        "source",
        (
            _listed("keep/old.txt", 61, "v-old"),
            _listed("keep/boundary.txt", 60, "v-boundary"),
            _listed("skip/old.txt", 90, "v-skip"),
        ),
    )

    manifest = build_archive_manifest(
        source,
        run_started_at_utc=datetime(2024, 4, 20, tzinfo=UTC),
        retention_days=60,
        versioning_state="Enabled",
        source_filter=SourcePathFilter("whitelist", ("keep/",)),
    )

    assert manifest.retention_cutoff_utc == datetime(2024, 2, 20, tzinfo=UTC)
    assert [(entry.key, entry.version_id) for entry in manifest.entries] == [
        ("keep/old.txt", "v-old")
    ]


@pytest.mark.unit()
def test_transfer_strategy_selection_and_fingerprint_verification() -> None:
    listed = _listed("key.txt", 70)
    entry = ManifestEntry("source", "key.txt", 10, listed.last_modified, '"etag"', "v1", listed)
    metadata = archive_metadata(entry)
    destination = replace(entry.object.properties, metadata=metadata)

    assert verify_destination(entry, destination).ok is True
    assert verify_destination(entry, replace(destination, size=11)).detail == "size mismatch"
    assert (
        select_transfer_strategy(10, S3TransferCapabilities(), simple_copy_limit_bytes=10)
        == "simple_native_copy"
    )
    assert (
        select_transfer_strategy(11, S3TransferCapabilities(), simple_copy_limit_bytes=10)
        == "multipart_native_copy"
    )
    assert (
        select_transfer_strategy(
            11,
            S3TransferCapabilities(native_copy=False),
            simple_copy_limit_bytes=10,
        )
        == "multipart_streaming"
    )
    assert (
        select_transfer_strategy(
            51,
            S3TransferCapabilities(native_copy=False, streaming_upload=False),
            streaming_limit_bytes=50,
        )
        == "temp_file_backed"
    )
    reserved = replace(listed, properties=_properties(metadata={FINGERPRINT_METADATA_KEY: "user"}))
    reserved_entry = ManifestEntry(
        "source", "key.txt", 10, listed.last_modified, None, "v1", reserved
    )
    with pytest.raises(ValueError, match="reserved key"):
        _ = archive_metadata(reserved_entry)


@pytest.mark.unit()
def test_run_archive_orders_phases_and_gates_cleanup() -> None:
    source = FakeBucket("source", (_listed("old.txt", 90, "v1"),))
    destination = FakeBucket("destination")
    decisions: list[tuple[str, str]] = []

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=False, max_workers=1),
        run_started_at_utc=datetime(2024, 4, 20, tzinfo=UTC),
        debug_logger=lambda entry, strategy: decisions.append((entry.key, strategy)),
    )

    assert result.ok is True
    assert destination.copied == ["old.txt"]
    assert source.deleted == []
    assert decisions == [("old.txt", "simple_native_copy")]

    cleanup_result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=datetime(2024, 4, 20, tzinfo=UTC),
    )

    assert cleanup_result.ok is True
    assert source.deleted == [("old.txt", "v1")]


@pytest.mark.unit()
def test_copy_or_verify_failure_blocks_later_phases() -> None:
    source = FakeBucket("source", (_listed("old.txt", 90),))
    failing_destination = FakeBucket("destination")
    failing_destination.fail_copy = True

    copy_failed = run_archive(
        source,
        failing_destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=2),
        run_started_at_utc=datetime(2024, 4, 20, tzinfo=UTC),
    )

    assert copy_failed.copy.ok is False
    assert copy_failed.verify.failures == ()
    assert source.deleted == []

    bad_destination = FakeBucket("destination", destination={"old.txt": _properties(size=10)})
    verify_failed = run_archive(
        source,
        bad_destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=datetime(2024, 4, 20, tzinfo=UTC),
    )

    assert verify_failed.copy.failures == ("old.txt: source fingerprint mismatch",)
    assert verify_failed.verify.failures == ()
    assert source.deleted == []


@pytest.mark.unit()
def test_run_archive_timeout_blocks_later_phases() -> None:
    started = datetime(2024, 4, 20, tzinfo=UTC)
    source = FakeBucket("source", (_listed("old.txt", 90),))
    destination = FakeBucket("destination")

    timed_out = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=started,
        clock=lambda: started + timedelta(days=8),
    )

    assert timed_out.copy.failures == ("archive run timed out",)
    assert timed_out.verify.failures == ()
    assert source.deleted == []


@pytest.mark.unit()
def test_key_only_cleanup_rechecks_source_before_delete() -> None:
    source = FakeBucket(
        "source",
        (_listed("old.txt", 90, None),),
        destination={"old.txt": _properties(size=11)},
    )
    destination = FakeBucket("destination")

    result = run_archive(
        source,
        destination,
        ArchiveOptions(retention_days=60, cleanup_enabled=True, max_workers=1),
        run_started_at_utc=datetime(2024, 4, 20, tzinfo=UTC),
    )

    assert result.cleanup.failures == ("old.txt: source changed before cleanup",)
    assert source.deleted == []


@pytest.mark.unit()
def test_options_cleanup_defaults() -> None:
    assert cleanup_enabled_from_env({}) is False
    assert cleanup_enabled_from_env({"ARCHIVER_ENABLE_CLEANUP": "true"}) is True
    assert ArchiveOptions.from_env({}).run_timeout == timedelta(days=7)
