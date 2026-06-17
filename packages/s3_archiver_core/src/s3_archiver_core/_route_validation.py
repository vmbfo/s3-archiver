"""Cross-route validation for ARCHIVER_CONFIG_JSON settings."""

from __future__ import annotations

from s3_archiver_core._archive_manifest_paths import route_path_prefix
from s3_archiver_core._settings_models import RouteSettings
from s3_archiver_core._settings_parse import EnvDecoder


def validate_bucket_whitelist(
    decoder: EnvDecoder, routes: tuple[RouteSettings, ...], whitelist: tuple[str, ...]
) -> None:
    """Fail when any route source/destination bucket is missing from the whitelist."""

    allowed = frozenset(whitelist)
    for route in routes:
        for side, location in (("source", route.source), ("destination", route.destination)):
            if location.bucket not in allowed:
                message = (
                    f"route {route.name!r} {side} bucket {location.bucket!r}"
                    + " is not in ARCHIVER_BUCKET_WHITELIST"
                )
                decoder.fail("ARCHIVER_BUCKET_WHITELIST", message)
                return


def validate_route_storage(decoder: EnvDecoder, routes: tuple[RouteSettings, ...]) -> None:
    """Reject identical source/destination storage and identical paths per side.

    Source and destination paths are checked symmetrically as two independent
    pairwise rules: two routes may not share the *same* source path on the same
    source storage, nor the *same* destination path on the same destination
    storage (regardless of their source paths). Nested and sibling paths are
    allowed on both sides; only exact path equality on shared storage is an error.
    """

    for route in routes:
        if route.source.storage_identity() == route.destination.storage_identity():
            decoder.fail(
                "ARCHIVER_CONFIG_JSON",
                f"route {route.name!r} source and destination storage locations must differ",
            )
            return
    for left_index, left in enumerate(routes):
        for right in routes[left_index + 1 :]:
            if left.source.storage_identity() == right.source.storage_identity() and (
                route_path_prefix(left.source.path) == route_path_prefix(right.source.path)
            ):
                decoder.fail(
                    "ARCHIVER_CONFIG_JSON",
                    f"routes {left.name!r} and {right.name!r} have identical source paths"
                    + " on the same storage",
                )
                return
            if left.destination.storage_identity() == right.destination.storage_identity() and (
                route_path_prefix(left.destination.path)
                == route_path_prefix(right.destination.path)
            ):
                decoder.fail(
                    "ARCHIVER_CONFIG_JSON",
                    f"routes {left.name!r} and {right.name!r} have identical destination paths"
                    + " on the same storage",
                )
                return
