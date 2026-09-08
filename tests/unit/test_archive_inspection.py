"""Streaming verification and safe restore regression tests."""

from __future__ import annotations

# pyright: reportPrivateUsage=false
import gzip
import hashlib
import io
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest
from s3_archiver_core.archive_group_metadata import ARCHIVE_SHA256_METADATA_KEY
from s3_archiver_core.archive_inspection import inspect_archive

from tests.unit.archive_workflow_fakes import FakeBucket, object_properties
from tests.unit.test_archive_refresh_safety import KEY, objects, run


@pytest.mark.unit()
def test_list_verify_and_selective_restore_read_the_real_archive(tmp_path: Path) -> None:
    entries = objects(0, 3)
    source = FakeBucket("source", entries, temp_dir=tmp_path)
    destination = FakeBucket("destination", temp_dir=tmp_path)
    assert run(source, destination).ok
    reported: list[tuple[str, str, int]] = []

    def report(key: str, name: str, size: int) -> None:
        reported.append((key, name, size))

    output = tmp_path / "restored"
    count, digest, size = inspect_archive(
        destination,
        KEY,
        report_member=report,
        output_dir=output,
        selected=frozenset({entries[1].key}),
    )
    assert count == 2
    assert digest == hashlib.sha256(destination.destination_payload(KEY)).hexdigest()
    assert size == len(destination.destination_payload(KEY))
    assert reported == [(entry.key, entry.key, entry.size) for entry in entries]
    assert not (output / entries[0].key).exists()
    assert (output / entries[1].key).read_bytes() == source.read_source_bytes(entries[1].key, "v1")
    assert inspect_archive(destination, KEY) == (count, digest, size)


@pytest.mark.unit()
@pytest.mark.parametrize("damage", ["hash", "size", "payload", "missing-member", "existing-output"])
def test_failed_restore_never_publishes_partial_files(tmp_path: Path, damage: str) -> None:
    destination = FakeBucket("destination", temp_dir=tmp_path)
    assert run(FakeBucket("source", objects(0), temp_dir=tmp_path), destination).ok
    props = destination._destination[KEY]
    selected: frozenset[str] = frozenset()
    output = tmp_path / "restored"
    if damage == "hash":
        destination._destination[KEY] = replace(
            props, metadata=dict(props.metadata) | {ARCHIVE_SHA256_METADATA_KEY: "bad"}
        )
    elif damage == "size":
        destination._destination[KEY] = replace(props, size=props.size + 1)
    elif damage == "payload":
        payload = bytearray(destination.destination_payload(KEY))
        payload[len(payload) // 2] ^= 1
        destination._destination_payloads[KEY] = bytes(payload)
    elif damage == "missing-member":
        selected = frozenset({"missing"})
    else:
        output.mkdir()
    with pytest.raises((ValueError, OSError, EOFError, tarfile.TarError)):
        _ = inspect_archive(destination, KEY, output_dir=output, selected=selected)
    assert not output.exists() or list(output.iterdir()) == []
    assert list(tmp_path.glob("s3-archiver-restore-*")) == []


@pytest.mark.unit()
@pytest.mark.parametrize("name,kind", [("../outside", tarfile.REGTYPE), ("link", tarfile.SYMTYPE)])
def test_restore_rejects_traversal_and_links(tmp_path: Path, name: str, kind: bytes) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name)
        info.type = kind
        info.linkname = "../outside"
        tar.addfile(info)
    payload = gzip.compress(buffer.getvalue())
    destination = FakeBucket(
        "destination",
        temp_dir=tmp_path,
        destination={
            KEY: object_properties(
                size=len(payload),
                metadata={ARCHIVE_SHA256_METADATA_KEY: hashlib.sha256(payload).hexdigest()},
            )
        },
        payloads={KEY: payload},
    )
    with pytest.raises(ValueError):
        _ = inspect_archive(destination, KEY, output_dir=tmp_path / "restored")
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "restored").exists()
