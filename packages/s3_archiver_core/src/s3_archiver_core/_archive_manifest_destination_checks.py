from __future__ import annotations

import sqlite3
from typing import cast

from s3_archiver_core._archive_manifest_sqlite import optional_row


def has_duplicate_direct_destination(connection: sqlite3.Connection) -> bool:
    return (
        optional_row(
            cast(
                object,
                connection.execute(
                    """
                    SELECT 1
                    FROM entries
                    WHERE copy_mode = 'direct'
                    GROUP BY destination_identity, destination_bucket, destination_key
                    HAVING COUNT(*) > 1
                    LIMIT 1
                    """
                ).fetchone(),
            )
        )
        is not None
    )


def has_duplicate_archive_destination(connection: sqlite3.Connection) -> bool:
    return (
        optional_row(
            cast(
                object,
                connection.execute(
                    """
                    SELECT 1
                    FROM archive_chunks
                    GROUP BY destination_identity, destination_bucket, chunk_destination_key
                    HAVING COUNT(*) > 1
                    LIMIT 1
                    """
                ).fetchone(),
            )
        )
        is not None
    )


def has_direct_archive_destination_collision(connection: sqlite3.Connection) -> bool:
    return (
        optional_row(
            cast(
                object,
                connection.execute(
                    """
                    SELECT 1
                    FROM entries AS direct
                    INNER JOIN archive_chunks AS chunk
                        ON chunk.destination_identity = direct.destination_identity
                        AND chunk.destination_bucket = direct.destination_bucket
                        AND chunk.chunk_destination_key = direct.destination_key
                    WHERE direct.copy_mode = 'direct'
                    LIMIT 1
                    """
                ).fetchone(),
            )
        )
        is not None
    )
