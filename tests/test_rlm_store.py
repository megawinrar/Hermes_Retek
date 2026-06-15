from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rlm_store  # noqa: E402
from sqlite_utils import connect as sqlite_connect  # noqa: E402


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

    with sqlite_connect(store) as con:
        raw_rows = con.execute("SELECT title, summary, content, tags_json, metadata_json FROM rlm_records").fetchall()
    assert secret not in json.dumps(raw_rows)


def test_get_record_kind_filter_and_artifact_fallback_ref(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    artifact = rlm_store.add_record(
        "artifact",
        "Browser export",
        "Saved authenticated export metadata",
        tags=("browser", "export", "browser"),
        process_id="proc-artifact",
        store_path=store,
    )
    rlm_store.add_record(
        "memory",
        "Browser note",
        "Same tag but different kind",
        tags=["browser"],
        process_id="proc-artifact",
        store_path=store,
    )

    found = rlm_store.get_record(artifact["id"], store_path=store)
    missing = rlm_store.get_record(999999, store_path=store)
    artifacts = rlm_store.search_records(tags=["browser"], kind="artifact", store_path=store)
    pack = rlm_store.build_context_pack(tags=["browser"], kind="artifact", token_budget=80, store_path=store)

    assert found is not None
    assert found["content"] == ""
    assert found["tags"] == ["browser", "export"]
    assert missing is None
    assert [record["id"] for record in artifacts] == [artifact["id"]]
    assert f"artifact=rlm:{artifact['id']}" in pack["context"]


def test_add_subcall_record_stores_child_agent_lifecycle(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    secret = "auth.sid=" + "S" * 32

    record = rlm_store.add_subcall_record(
        parent_process_id="proc-parent",
        child_agent_id="sa-1",
        parent_agent_id="sa-parent",
        depth=1,
        status="timeout",
        goal=f"Investigate supplier portal with {secret}",
        summary="Timed out before result",
        timeout_seconds=120,
        token_budget=900,
        api_calls=3,
        duration_seconds=121.4,
        metadata={"raw": secret, "exit_reason": "timeout"},
        store_path=store,
    )
    found = rlm_store.search_records(kind="subcall", process_id="proc-parent", store_path=store)

    assert record["kind"] == "subcall"
    assert record["metadata"]["child_agent_id"] == "sa-1"
    assert record["metadata"]["parent_agent_id"] == "sa-parent"
    assert record["metadata"]["timeout_seconds"] == 120.0
    assert record["metadata"]["duration_seconds"] == 121.4
    assert {"subcall", "status:timeout", "child:sa-1", "parent:sa-parent", "process:proc-parent"} <= set(
        record["tags"]
    )
    assert found[0]["id"] == record["id"]
    assert secret not in json.dumps(record, ensure_ascii=False)


def test_parse_metadata_rejects_non_object_and_bad_json() -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        rlm_store._parse_metadata('["not", "an", "object"]')
    with pytest.raises(json.JSONDecodeError):
        rlm_store._parse_metadata("{not-json")


def test_cli_add_and_pack_with_metadata_and_repeated_tags(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    add = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "rlm_store.py"),
            "--store",
            str(store),
            "add",
            "--kind",
            "artifact",
            "--title",
            "Timing report",
            "--summary",
            "Restart timing evidence",
            "--content",
            "duration_ms=123",
            "--tags",
            "ops/restart, report",
            "--tag",
            "timing",
            "--process-id",
            "proc-cli",
            "--metadata",
            '{"path":"reports/timing.json"}',
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert add.returncode == 0, add.stderr
    added = json.loads(add.stdout)
    assert added["kind"] == "artifact"
    assert added["tags"] == ["ops/restart", "report", "timing"]
    assert added["metadata"]["path"] == "reports/timing.json"

    pack = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "rlm_store.py"),
            "--store",
            str(store),
            "pack",
            "--tags",
            "ops/restart",
            "--process-id",
            "proc-cli",
            "--kind",
            "artifact",
            "--token-budget",
            "80",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert pack.returncode == 0, pack.stderr
    payload = json.loads(pack.stdout)
    assert payload["records"][0]["id"] == added["id"]
    assert "artifact=reports/timing.json" in payload["context"]
