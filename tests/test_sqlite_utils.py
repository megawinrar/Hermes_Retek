from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import process_orchestrator  # noqa: E402
import rlm_store  # noqa: E402
import sqlite_utils  # noqa: E402
import supervisor_common  # noqa: E402


def assert_closed_after_context(con: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        con.execute("SELECT 1")


def test_sqlite_utils_context_manager_closes_connection(tmp_path: Path) -> None:
    store = tmp_path / "plain.db"

    with sqlite_utils.connect(store) as con:
        con.execute("CREATE TABLE example(id INTEGER PRIMARY KEY)")

    assert_closed_after_context(con)


def test_storage_connectors_close_after_context_manager(tmp_path: Path) -> None:
    stores = [
        (supervisor_common.connect, tmp_path / "supervisor.db"),
        (process_orchestrator.connect, tmp_path / "process.db"),
        (rlm_store.connect, tmp_path / "rlm.db"),
    ]

    for connect, store in stores:
        with connect(store) as con:
            con.execute("SELECT 1").fetchone()

        assert_closed_after_context(con)
