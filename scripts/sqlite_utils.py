from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class ClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager also closes the handle.

    The standard sqlite3 connection commits or rolls back on ``with`` exit but
    intentionally leaves the database handle open. Most Hermes stores use
    short-lived connections, so closing on context exit prevents descriptor
    leaks in long-running gateway and cron processes.
    """

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool | None:
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


def connect(path: Path | str, **kwargs: Any) -> ClosingConnection:
    return sqlite3.connect(path, factory=ClosingConnection, **kwargs)
