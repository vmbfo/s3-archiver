"""Route manifest custom parser context tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import pytest
from s3_archiver_core.archive_manifest import (
    ArchiveManifest,
    ArchiveManifestRoute,
    ParserContext,
    ParserSelector,
    SelectedObject,
    build_route_archive_manifest,
)
from s3_archiver_core.s3 import S3ListedObject

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed
from tests.unit.archive_workflow_fakes import object_properties as _properties

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)


@pytest.mark.unit()
def test_route_manifest_passes_parser_context_with_hydrated_properties() -> None:
    listed = _listed("data/custom.txt", 1, "v-context")
    properties = _properties(
        metadata={"dataset": "raw"},
        tags={"route": "custom"},
        checksums={"sha256": "checksum"},
        checksum_type="FULL_OBJECT",
    )
    listed = S3ListedObject(
        listed.key,
        properties.size,
        listed.last_modified,
        listed.etag,
        listed.version_id,
        properties,
    )
    seen: list[tuple[str, str, str | None]] = []

    def parser(listed_object: S3ListedObject, context: ParserContext) -> SelectedObject:
        assert context.listed is listed_object
        assert context.properties == properties
        context_properties = context.properties
        assert context_properties is not None
        seen.append(
            (
                context_properties.metadata["dataset"],
                context_properties.tags["route"],
                context_properties.checksums["sha256"],
            )
        )
        return SelectedObject(datetime(2026, 4, 13, tzinfo=UTC), "last_modified")

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "custom",
                FakeBucket("source", (listed,)),
                FakeBucket("archive"),
                parser=parser,
                parser_kind="direct",
                copy_mode="direct",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert [entry.key for entry in manifest.entries] == ["data/custom.txt"]
    assert seen == [("raw", "custom", "checksum")]


@pytest.mark.unit()
def test_route_manifest_supports_varargs_and_opaque_legacy_parser_signatures() -> None:
    listed = _listed("data/custom.txt", 1, "v-context")
    calls: list[int] = []

    def varargs_parser(*args: object) -> SelectedObject:
        calls.append(len(args))
        return SelectedObject(datetime(2026, 4, 13, tzinfo=UTC), "last_modified")

    def keyword_parser(
        listed_object: S3ListedObject, *, context: ParserContext | None = None
    ) -> SelectedObject:
        assert listed_object is listed
        assert context is None
        calls.append(1)
        return SelectedObject(datetime(2026, 4, 13, tzinfo=UTC), "last_modified")

    class OpaqueLegacyParser:
        @property
        def __signature__(self) -> object:
            raise ValueError("opaque")

        def __call__(self, listed_object: S3ListedObject) -> SelectedObject:
            assert listed_object is listed
            calls.append(1)
            return SelectedObject(datetime(2026, 4, 13, tzinfo=UTC), "last_modified")

    varargs_manifest = _manifest(cast_parser(varargs_parser), listed)
    keyword_manifest = _manifest(cast_parser(keyword_parser), listed)
    legacy_manifest = _manifest(cast_parser(OpaqueLegacyParser()), listed)

    assert [entry.key for entry in varargs_manifest.entries] == ["data/custom.txt"]
    assert [entry.key for entry in keyword_manifest.entries] == ["data/custom.txt"]
    assert [entry.key for entry in legacy_manifest.entries] == ["data/custom.txt"]
    assert calls == [2, 1, 1]


def cast_parser(value: object) -> ParserSelector:
    return cast(ParserSelector, value)


def _manifest(parser: ParserSelector, listed: S3ListedObject) -> ArchiveManifest:
    return build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "custom",
                FakeBucket("source", (listed,)),
                FakeBucket("archive"),
                parser=parser,
                parser_kind="direct",
                copy_mode="direct",
            ),
        ),
        run_started_at_utc=STARTED,
    )
