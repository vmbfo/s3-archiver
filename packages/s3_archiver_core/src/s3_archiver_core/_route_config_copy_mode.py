"""Copy-mode decoding for ARCHIVER_CONFIG_JSON route settings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from s3_archiver_core._route_config_fields import required_string as _required_string
from s3_archiver_core._settings_models import CopyMode
from s3_archiver_core._settings_parse import EnvDecoder
from s3_archiver_core.parsers.kinds import ParserKind


def load_copy_mode_config(
    decoder: EnvDecoder,
    route: Mapping[str, object],
    field: str,
    parser: ParserKind,
) -> tuple[CopyMode, int] | None:
    value = route.get("copy_mode")
    if isinstance(value, dict):
        copy_mode = _load_copy_mode_object(decoder, cast(Mapping[str, object], value), field)
        if copy_mode is None:
            return None
        return _validate_grouping_parser(decoder, parser, copy_mode, field)
    copy_mode = _load_copy_mode(decoder, route, field)
    if copy_mode is None:
        return None
    return copy_mode, 0


def _load_copy_mode(
    decoder: EnvDecoder, route: Mapping[str, object], field: str
) -> CopyMode | None:
    value = _required_string(decoder, route, "copy_mode", field)
    return _copy_mode_enum(decoder, value, field)


def _load_copy_mode_object(
    decoder: EnvDecoder, value: Mapping[str, object], field: str
) -> tuple[CopyMode, int] | None:
    copy_type = _required_string(decoder, value, "type", f"{field}.type")
    copy_mode = _copy_mode_enum(decoder, copy_type, f"{field}.type")
    if copy_mode is None:
        return None
    group_after_timestamp_parts = _copy_mode_non_negative_int(
        decoder,
        value.get("group_after_timestamp_parts", 0),
        f"{field}.group_after_timestamp_parts",
    )
    if group_after_timestamp_parts is None:
        return None
    return copy_mode, group_after_timestamp_parts


def _copy_mode_enum(decoder: EnvDecoder, value: str | None, field: str) -> CopyMode | None:
    valid = frozenset({mode.value for mode in CopyMode})
    if value is None:
        return None
    if value not in valid:
        decoder.fail(field, f"{field} must be one of {valid}, got {value!r}")
        return None
    return CopyMode(value)


def _copy_mode_non_negative_int(decoder: EnvDecoder, value: object, field: str) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        decoder.fail(field, f"{field} must be a non-negative integer")
        return None
    if value < 0:
        decoder.fail(field, f"{field} must be a non-negative integer")
        return None
    return value


def _validate_grouping_parser(
    decoder: EnvDecoder,
    parser: ParserKind,
    copy_mode: tuple[CopyMode, int],
    field: str,
) -> tuple[CopyMode, int] | None:
    if copy_mode[1] > 0 and parser != ParserKind.FOLDER_TIMESTAMP:
        decoder.fail(
            f"{field}.group_after_timestamp_parts",
            f"{field}.group_after_timestamp_parts requires parser=folder_timestamp",
        )
        return None
    return copy_mode
