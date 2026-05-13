from __future__ import annotations

import sqlite3
from typing import cast

from s3_archiver_core._archive_manifest_sqlite import required_row


def count_entries(connection: sqlite3.Connection) -> int:
    row = required_row(cast(object, connection.execute("SELECT COUNT(*) FROM entries").fetchone()))
    return int(cast(int, row[0]))


def count_skipped(connection: sqlite3.Connection) -> int:
    row = required_row(cast(object, connection.execute("SELECT COUNT(*) FROM skipped").fetchone()))
    return int(cast(int, row[0]))
