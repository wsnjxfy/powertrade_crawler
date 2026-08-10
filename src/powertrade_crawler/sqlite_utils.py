from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def sqlite_row_connection(path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a row-enabled connection and always release its Windows file handle."""
    connection = sqlite3.connect(path, timeout=60)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()
