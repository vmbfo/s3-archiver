"""Parser registry."""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Iterable, Mapping
from functools import cache
from types import ModuleType
from typing import cast

import s3_archiver_core.parsers as _parsers_package
from s3_archiver_core.parsers.kinds import ParserKind
from s3_archiver_core.parsers.protocol import ObjectParser

ParserFactory = Callable[[], ObjectParser]

_EXCLUDED_MODULES = frozenset({"template"})


def parser_for_kind(kind: ParserKind | str) -> ObjectParser:
    """Return a new parser for a registered kind."""

    parser_kind = ParserKind(str(kind))
    try:
        factory = _registry()[parser_kind]
    except KeyError as exc:
        raise ValueError(f"unsupported parser kind {parser_kind!r}") from exc
    return factory()


def registered_parser_kinds() -> frozenset[ParserKind]:
    """Return registered parser kinds."""

    return frozenset(_registry())


def clear_parser_registry_cache() -> None:
    """Clear cached parser discovery results."""

    _registry.cache_clear()


@cache
def _registry() -> Mapping[ParserKind, ParserFactory]:
    return discover_parser_factories(_iter_parser_module_names(), importlib.import_module)


def _iter_parser_module_names() -> tuple[str, ...]:
    package_paths = cast(Iterable[str], _parsers_package.__path__)
    names: list[str] = []
    for module in pkgutil.iter_modules(package_paths):
        if module.ispkg or module.name in _EXCLUDED_MODULES:
            continue
        names.append(f"{_parsers_package.__name__}.{module.name}")
    return tuple(names)


def discover_parser_factories(
    module_names: Iterable[str],
    import_module: Callable[[str], ModuleType],
) -> Mapping[ParserKind, ParserFactory]:
    """Return parser factories discovered from importable parser modules."""

    registry: dict[ParserKind, ParserFactory] = {}
    for module_name in module_names:
        parser_name = module_name.rsplit(".", maxsplit=1)[-1]
        if parser_name in _EXCLUDED_MODULES:
            continue
        module = import_module(module_name)
        module_items = cast(Mapping[str, object], module.__dict__)
        parser = module_items.get("Parser")
        if parser is None or not callable(parser):
            continue
        registry[ParserKind(parser_name)] = cast(ParserFactory, parser)
    return registry
