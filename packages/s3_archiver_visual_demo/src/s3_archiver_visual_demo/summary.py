"""Verified-result terminal summary for the manual visual demo."""

from __future__ import annotations


def print_verified_summary(
    payload: dict[str, object],
    *,
    total_count: int,
    copied_count: int,
    remaining_source_count: int,
) -> None:
    """Print the verified result summary after the demo run completes."""

    print()
    print("=" * 78)
    print("VERIFIED RESULT")
    print("=" * 78)
    print(f"  status: {payload['status']}")
    print(f"  source objects seeded: {total_count}")
    print(f"  remaining in source after archive: {remaining_source_count}")
    print(f"  archived to destination: {copied_count}")
