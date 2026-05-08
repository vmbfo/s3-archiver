"""Route manifest parser module context tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import ModuleType

import pytest
from s3_archiver_core.archive_manifest import (
    ArchiveManifestRoute,
    ParserContext,
    build_route_archive_manifest,
)
from s3_archiver_core.parsers import registry
from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.protocol import ParserListedObject
from s3_archiver_core.parsers.results import SelectedObject
from s3_archiver_core.s3 import S3ListedObject

from tests.unit.archive_workflow_fakes import FakeBucket
from tests.unit.archive_workflow_fakes import listed_object as _listed
from tests.unit.archive_workflow_fakes import object_properties as _properties

STARTED = datetime(2026, 4, 27, 12, tzinfo=UTC)


@pytest.mark.unit()
def test_route_manifest_passes_parser_context_with_hydrated_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    parser_kind = ParserKind("custom_context")
    seen: list[tuple[str, str, str | None]] = []

    class Parser:
        def parse(
            self, listed_object: ParserListedObject, context: ParserContext
        ) -> SelectedObject:
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
            return SelectedObject(
                datetime(2026, 4, 13, tzinfo=UTC),
                "last_modified",
                "data",
            )

    monkeypatch.setattr(registry, "_registry", lambda: {parser_kind: Parser})

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "custom",
                FakeBucket("source", (listed,)),
                FakeBucket("archive"),
                parser_kind=parser_kind,
                copy_mode="daily_tar_gz",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert [entry.key for entry in manifest.entries] == ["data/custom.txt"]
    assert seen == [("raw", "custom", "checksum")]


@pytest.mark.unit()
def test_parser_module_discovery_registers_parser_class_by_filename() -> None:
    module = ModuleType("s3_archiver_core.parsers.customer_timestamp")

    class Parser:
        def parse(self, listed: ParserListedObject, context: ParserContext) -> SelectedObject:
            _ = (listed, context)
            return SelectedObject(datetime(2026, 4, 13, tzinfo=UTC), "basename", "")

    module.__dict__["Parser"] = Parser

    factories = registry.discover_parser_factories(
        ("s3_archiver_core.parsers.customer_timestamp",),
        lambda _name: module,
    )

    parser = factories[ParserKind("customer_timestamp")]()
    assert isinstance(parser, Parser)


@pytest.mark.unit()
def test_route_manifest_converts_parser_value_errors_to_object_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parser_kind = ParserKind("value_error_parser")

    class Parser:
        def parse(self, listed: ParserListedObject, context: ParserContext) -> SelectedObject:
            _ = context
            if listed.key == "data/bad.txt":
                raise ValueError("bad parser input")
            return SelectedObject(datetime(2026, 4, 13, tzinfo=UTC), "basename", "")

    monkeypatch.setattr(registry, "_registry", lambda: {parser_kind: Parser})

    manifest = build_route_archive_manifest(
        (
            ArchiveManifestRoute(
                "custom",
                FakeBucket(
                    "source",
                    (
                        _listed("data/good.txt", 1, None),
                        _listed("data/bad.txt", 1, None),
                    ),
                ),
                FakeBucket("archive"),
                parser_kind=parser_kind,
                copy_mode="direct",
            ),
        ),
        run_started_at_utc=STARTED,
    )

    assert [entry.key for entry in manifest.entries] == ["data/good.txt"]
    assert [(item.key, item.reason) for item in manifest.skipped_objects] == [
        ("data/bad.txt", "parser error: bad parser input")
    ]
