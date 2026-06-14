from __future__ import annotations

import json
import math
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rlm_store  # noqa: E402


def test_schema_initializes_store(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"

    with rlm_store.connect(store) as con:
        tables = {
            row["name"]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        version = con.execute("SELECT value FROM rlm_schema_meta WHERE key = 'schema_version'").fetchone()

    assert "rlm_records" in tables
    assert "rlm_schema_meta" in tables
    assert version["value"] == str(rlm_store.SCHEMA_VERSION)


def test_add_and_search_by_tag_process_and_cli_json(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    rlm_store.add_record(
        "memory",
        "Restart checklist",
        "Safe restart keeps active process state intact",
        tags=["ops/restart", "safe"],
        process_id="proc-1",
        store_path=store,
    )
    rlm_store.add_record(
        "event",
        "Unrelated event",
        "Different process",
        tags=["ops/other"],
        process_id="proc-2",
        store_path=store,
    )

    results = rlm_store.search_records(tags=["ops/restart"], process_id="proc-1", store_path=store)

    assert [record["title"] for record in results] == ["Restart checklist"]
    assert results[0]["tags"] == ["ops/restart", "safe"]

    cli = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "rlm_store.py"),
            "--store",
            str(store),
            "search",
            "--query",
            "restart",
            "--tag",
            "ops/restart",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert cli.returncode == 0, cli.stderr
    payload = json.loads(cli.stdout)
    assert payload[0]["title"] == "Restart checklist"


def test_context_pack_respects_token_budget_and_includes_artifact_ref(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    rlm_store.add_record(
        "artifact",
        "Timing report",
        "Contains restart duration evidence and operator-visible measurements",
        tags=["ops/restart"],
        process_id="proc-pack",
        metadata={"path": "reports/timing.json"},
        importance=0.9,
        store_path=store,
    )
    rlm_store.add_record(
        "memory",
        "Long note",
        "x" * 400,
        tags=["ops/restart"],
        process_id="proc-pack",
        store_path=store,
    )

    pack = rlm_store.build_context_pack(
        tags=["ops/restart"],
        process_id="proc-pack",
        token_budget=30,
        store_path=store,
    )

    assert pack["estimated_tokens"] <= 30
    assert math.ceil(len(pack["context"]) / 4) <= 30
    assert "artifact=reports/timing.json" in pack["context"]
    assert "x" * 100 not in pack["context"]


def test_redaction_prevents_raw_secrets_in_storage_and_search_output(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    secret = "Authorization: Bearer " + "C" * 30

    record = rlm_store.add_record(
        "memory",
        f"Token note {secret}",
        f"Summary has {secret}",
        content=f"Raw content has {secret}",
        tags=[f"secret/{secret}"],
        metadata={"path": f"/tmp/{secret}"},
        process_id="proc-secret",
        store_path=store,
    )
    found = rlm_store.search_records(query="Token", process_id="proc-secret", store_path=store)

    assert secret not in json.dumps(record)
    assert secret not in json.dumps(found)
    assert "[REDACTED]" in record["content"]
    assert "[REDACTED]" in json.dumps(found)

    with sqlite3.connect(store) as con:
        raw_rows = con.execute("SELECT title, summary, content, tags_json, metadata_json FROM rlm_records").fetchall()
    assert secret not in json.dumps(raw_rows)
