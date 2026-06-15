from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import process_rlm_memory  # noqa: E402
import rlm_store  # noqa: E402
from sqlite_utils import connect as sqlite_connect  # noqa: E402


def args_for_rlm(store: Path | None = None, *, enabled: bool = False) -> argparse.Namespace:
    return argparse.Namespace(rlm_store=store, rlm_enabled=enabled)


def supplier_route() -> dict[str, object]:
    return {
        "task_level": "L2",
        "task_type": "supplier_price_deadline_analysis",
        "risk_level": "high",
        "human_gate_required": False,
    }


def browser_skill_context() -> dict[str, object]:
    browser_skill = {"name": "hermes-browser", "path": "skills/hermes-browser/SKILL.md"}
    return {
        "selected_skills": [browser_skill],
        "task_tags": ["browser", "supplier", "research"],
        "roles": {"bot1": [browser_skill], "tester": [browser_skill]},
        "gated_roles": {},
        "runtime_contract": {"load_only_selected_skill_paths": True},
    }


def test_write_enabled_uses_store_flag_and_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("HERMES_RLM_ENABLED", raising=False)

    assert process_rlm_memory.write_enabled(args_for_rlm()) is False
    assert process_rlm_memory.write_enabled(args_for_rlm(tmp_path / "rlm.db")) is True
    assert process_rlm_memory.write_enabled(args_for_rlm(enabled=True)) is True
    assert process_rlm_memory.config_from_args(args_for_rlm(tmp_path / "rlm.db")).store_path == str(tmp_path / "rlm.db")

    monkeypatch.setenv("HERMES_RLM_ENABLED", "1")
    assert process_rlm_memory.write_enabled(args_for_rlm()) is True


def test_write_records_creates_process_bot_review_and_browser_skill_records(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    records = process_rlm_memory.write_records(
        args=args_for_rlm(store),
        process_id="proc-rlm",
        supervisor_task_id="sup-rlm",
        task="Проверь закупки Р6М5",
        acceptance="Need supplier evidence.",
        route=supplier_route(),
        skill_context=browser_skill_context(),
        final_status="approved",
        bot1_result="Bot1 found supplier evidence.",
        bot2_session_id="bot2-session",
        verdict={"status": "APPROVE", "summary": "Evidence checked."},
    )

    kinds = {record["kind"] for record in records}
    tags = {tag for record in records for tag in record["tags"]}
    persisted = rlm_store.search_records(process_id="proc-rlm", store_path=store, limit=20)

    assert {"process_summary", "bot_output", "bot_review", "skill_usage"} <= kinds
    assert "skill/hermes-browser" in tags
    assert "task/browser" in tags
    assert len(persisted) == len(records)


def test_records_event_payload_is_compact_and_points_to_store(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    records = [
        {"id": 1, "kind": "process_summary"},
        {"id": 2, "kind": "bot_output"},
    ]

    payload = process_rlm_memory.records_event_payload(args_for_rlm(store), records)

    assert payload == {
        "record_ids": [1, 2],
        "record_kinds": ["process_summary", "bot_output"],
        "store_path": str(store),
    }


def test_write_process_records_uses_snapshot_and_config(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    snapshot = process_rlm_memory.ProcessRlmSnapshot(
        process_id="proc-snapshot",
        supervisor_task_id="sup-snapshot",
        task="Проверь закупки",
        acceptance="Need evidence.",
        route=supplier_route(),
        skill_context=browser_skill_context(),
        final_status="approved",
        bot1_result="Bot1 evidence",
        bot2_session_id="bot2-snapshot",
        verdict={"status": "APPROVE", "summary": "ok"},
    )

    outcome = process_rlm_memory.safe_write_process_records(
        snapshot,
        process_rlm_memory.RlmConfig(enabled=True, store_path=str(store)),
    )

    assert outcome.status == "ok"
    assert outcome.event_type == "rlm_records_written"
    assert outcome.payload["store_path"] == str(store)
    assert rlm_store.search_records(process_id="proc-snapshot", store_path=store)


def test_safe_write_process_records_is_non_blocking_and_redacts_error(monkeypatch, tmp_path: Path) -> None:
    secret = "tok_" + "E" * 32

    def fail_add_record(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError(f"cannot write {secret}")

    monkeypatch.setattr(process_rlm_memory.rlm_store, "add_record", fail_add_record)
    snapshot = process_rlm_memory.ProcessRlmSnapshot(
        process_id="proc-fail",
        supervisor_task_id="sup-fail",
        task="task",
        acceptance="acceptance",
        route=supplier_route(),
        skill_context=browser_skill_context(),
        final_status="approved",
        bot1_result="result",
        bot2_session_id="bot2-fail",
    )

    outcome = process_rlm_memory.safe_write_process_records(
        snapshot,
        process_rlm_memory.RlmConfig(enabled=True, store_path=str(tmp_path / "rlm.db")),
    )

    assert outcome.status == "error"
    assert outcome.event_type == "rlm_write_failed"
    assert secret not in json.dumps(outcome.payload)
    assert "[REDACTED]" in outcome.payload["error"]


def test_write_records_redacts_secret_like_values(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    secret = "tok_" + "D" * 32
    process_rlm_memory.write_records(
        args=args_for_rlm(store),
        process_id="proc-secret",
        supervisor_task_id="sup-secret",
        task=f"Use API_KEY='{secret}'",
        acceptance="No secret leaks.",
        route={"task_level": "L4", "task_type": "code_change", "risk_level": "high", "human_gate_required": True},
        skill_context={"selected_skills": [], "task_tags": ["code"]},
        final_status="awaiting_human_decision",
        bot1_result=f"Bot1 output API_KEY='{secret}'",
        bot2_session_id="bot2-secret",
        verdict={"status": "NEEDS_HUMAN", "summary": f"Secret {secret} must be hidden."},
        human_message=f"Human gate for {secret}",
    )

    with sqlite_connect(store) as con:
        raw = json.dumps(con.execute("SELECT title, summary, content, tags_json, metadata_json FROM rlm_records").fetchall())

    assert secret not in raw
    assert "[REDACTED]" in raw


def test_truncate_content_preserves_marker() -> None:
    text = "x" * 80

    assert process_rlm_memory.truncate_content(text, limit=40).endswith("...[truncated for RLM]")
