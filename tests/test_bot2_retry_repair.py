from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_bot_lab  # noqa: E402
from process_orchestrator import live_dual_result  # noqa: E402
from supervisor_common import MAX_BOT_REVIEW_CYCLES  # noqa: E402


def test_bot2_repair_prompt_requires_json_only() -> None:
    messages = dual_bot_lab.bot2_repair_messages(
        "Change code",
        "Need tests and evidence",
        "Bot#1 result",
        "Here is prose without JSON",
    )

    assert messages[0]["role"] == "system"
    assert "Return ONLY one valid JSON object" in messages[0]["content"]
    assert "Do not include Markdown" in messages[0]["content"]
    assert "Return ONLY valid JSON matching this schema" in messages[1]["content"]
    assert "MISSING_TESTS_FOR_CODE_CHANGE" in messages[1]["content"]


def test_bot2_primary_prompt_requires_json_only() -> None:
    messages = dual_bot_lab.bot2_messages(
        "Change code",
        "Need tests and evidence",
        "Bot#1 result",
    )

    assert messages[0]["role"] == "system"
    assert "Return ONLY one valid JSON object" in messages[0]["content"]
    assert "Do not include Markdown" in messages[0]["content"]
    assert "Return ONLY valid JSON matching this schema" in messages[1]["content"]
    assert "## Bot#2 Review" not in messages[1]["content"]
    assert "## Verdict JSON" not in messages[1]["content"]


def test_bot2_primary_prompt_requires_concise_defect_review() -> None:
    messages = dual_bot_lab.bot2_messages(
        "Plan migration",
        "Need rollback and tests",
        "Bot#1 result",
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "Bot#2 is a defect reviewer, not a second implementer" in combined
    assert "Do not solve the task again" in combined
    assert "Return compact one-line JSON" in combined
    assert "risks: max 3 items" in combined
    assert "required_fixes: max 3 actionable items" in combined


def test_bot2_repair_prompt_keeps_repaired_verdict_concise() -> None:
    messages = dual_bot_lab.bot2_repair_messages(
        "Plan migration",
        "Need rollback and tests",
        "Bot#1 result",
        "invalid prose",
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "Keep the repaired verdict concise" in combined
    assert "Bot#2 concise defect-review rules" in combined


def test_semantic_budget_profiles_scale_meaning_by_level() -> None:
    l1 = dual_bot_lab.semantic_budget_for_route({"task_level": "L1", "risk_level": "low"}, "bot1")
    l4 = dual_bot_lab.semantic_budget_for_route({"task_level": "L4", "human_gate_required": True}, "bot2")

    assert l1["policy"] == "meaning-first compression"
    assert l1["depth"] == "compact_task_answer"
    assert "multi-phase plan" in l1["omit"]
    assert l4["depth"] == "production_or_human_gate"
    assert l4["issue_budget"] == 3
    assert "unsafe action" in l4["must_focus"]


def test_live_dual_result_repairs_invalid_bot2_json_once(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[dict[str, str]]] = []
    messages: list[tuple[str, str]] = []

    def fake_call_chat(
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        timeout: int,
    ) -> tuple[str, dict[str, object]]:
        calls.append(messages)
        if len(calls) == 1:
            return "Bot#1 implementation result", {"usage": {"total_tokens": 10}}
        if len(calls) == 2:
            return "Looks fine, ship it.", {"usage": {"total_tokens": 12}}
        return (
            '{"status":"APPROVE","approved_action":"execute","summary":"repair ok",'
            '"evidence_checked":["bot1 result"],"risks":[],"required_fixes":[],"confidence":0.8}',
            {"usage": {"total_tokens": 14}},
        )

    def fake_add_message(run_id: str, speaker: str, model: str, content: str, metadata: dict[str, object]) -> None:
        messages.append((speaker, content))

    monkeypatch.setattr(dual_bot_lab, "bothub_config", lambda: {"base_url": "https://example.test/v1", "api_key": "test"})
    monkeypatch.setattr(dual_bot_lab, "run_id", lambda: "dual-test")
    monkeypatch.setattr(dual_bot_lab, "add_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "call_chat", fake_call_chat)
    monkeypatch.setattr(dual_bot_lab, "add_message", fake_add_message)
    monkeypatch.setattr(dual_bot_lab, "update_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "write_report", lambda **kwargs: tmp_path / "report.md")

    bot1, run_id, verdict, report_path = live_dual_result(
        "Change code and add tests",
        "Need tests and evidence",
        bot1_model="bot1-model",
        bot2_model="bot2-model",
        max_tokens=100,
        timeout=10,
    )

    assert bot1 == "Bot#1 implementation result"
    assert run_id == "dual-test"
    assert report_path.endswith("report.md")
    assert verdict["status"] == "APPROVE"
    assert verdict["summary"] == "repair ok"
    assert verdict["repair_attempted"] is True
    assert verdict["repair_status"] == "repaired"
    assert verdict["semantic_budget"]["bot2"]["policy"] == "meaning-first compression"
    assert verdict["review_cycles"][0]["semantic_budget"]["bot2"]["issue_budget"] == 3
    assert verdict["review_cycles"][0]["bot2_repair_attempted"] is True
    assert verdict["review_cycles"][0]["bot2_repair_status"] == "repaired"
    assert len(calls) == 3
    assert "Semantic budget" in calls[0][1]["content"]
    assert "Semantic budget" in calls[1][1]["content"]
    assert "Return ONLY valid JSON matching this schema" in calls[2][1]["content"]
    assert [speaker for speaker, _content in messages] == ["Bot#1", "Bot#2-1", "Bot#2-repair-1"]


def test_live_dual_result_applies_per_role_token_caps(monkeypatch, tmp_path: Path) -> None:
    seen_tokens: list[int] = []

    def fake_call_chat(
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        timeout: int,
    ) -> tuple[str, dict[str, object]]:
        seen_tokens.append(max_tokens)
        if len(seen_tokens) == 1:
            return "Bot#1 implementation result", {"usage": {"total_tokens": 10}}
        if len(seen_tokens) == 2:
            return "Bot#2 prose without JSON", {"usage": {"total_tokens": 12}}
        return (
            '{"status":"APPROVE","approved_action":"execute","summary":"repair ok",'
            '"evidence_checked":["bot1 result"],"risks":[],"required_fixes":[],"confidence":0.8}',
            {"usage": {"total_tokens": 14}},
        )

    monkeypatch.setenv("HERMES_BOT2_VERDICT_MAX_TOKENS", "333")
    monkeypatch.setenv("HERMES_BOT2_REPAIR_MAX_TOKENS", "222")
    monkeypatch.setattr(dual_bot_lab, "bothub_config", lambda: {"base_url": "https://example.test/v1", "api_key": "test"})
    monkeypatch.setattr(dual_bot_lab, "run_id", lambda: "dual-test")
    monkeypatch.setattr(dual_bot_lab, "add_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "call_chat", fake_call_chat)
    monkeypatch.setattr(dual_bot_lab, "add_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "update_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "write_report", lambda **kwargs: tmp_path / "report.md")

    live_dual_result(
        "Change code and add tests",
        "Need tests and evidence",
        bot1_model="bot1-model",
        bot2_model="bot2-model",
        max_tokens=1200,
        timeout=10,
    )

    assert seen_tokens == [1200, 333, 222]


def test_live_dual_result_stays_fail_closed_when_repair_is_invalid(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[dict[str, str]]] = []

    def fake_call_chat(
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        timeout: int,
    ) -> tuple[str, dict[str, object]]:
        calls.append(messages)
        if len(calls) == 1:
            return "Bot#1 implementation result", {"usage": {"total_tokens": 10}}
        if len(calls) == 2:
            return "Looks fine, ship it.", {"usage": {"total_tokens": 12}}
        return "Still not JSON", {"usage": {"total_tokens": 14}}

    monkeypatch.setattr(dual_bot_lab, "bothub_config", lambda: {"base_url": "https://example.test/v1", "api_key": "test"})
    monkeypatch.setattr(dual_bot_lab, "run_id", lambda: "dual-test")
    monkeypatch.setattr(dual_bot_lab, "add_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "call_chat", fake_call_chat)
    monkeypatch.setattr(dual_bot_lab, "add_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "update_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "write_report", lambda **kwargs: tmp_path / "report.md")

    _bot1, _run_id, verdict, _report_path = live_dual_result(
        "Change code and add tests",
        "Need tests and evidence",
        bot1_model="bot1-model",
        bot2_model="bot2-model",
        max_tokens=100,
        timeout=10,
    )

    assert verdict["status"] == "INVALID_BOT2_OUTPUT"
    assert verdict["repair_attempted"] is True
    assert verdict["repair_status"] == "failed_closed"
    assert verdict["review_cycles"][0]["bot2_repair_attempted"] is True
    assert verdict["review_cycles"][0]["bot2_repair_status"] == "failed_closed"
    assert len(calls) == 3


def test_live_dual_result_records_max_cycle_exhaustion(monkeypatch, tmp_path: Path) -> None:
    calls: list[list[dict[str, str]]] = []
    speakers: list[str] = []

    def request_changes(summary: str, fix: str) -> str:
        return (
            '{"status":"REQUEST_CHANGES","approved_action":"needs_revision",'
            f'"summary":"{summary}","evidence_checked":["bot1"],'
            f'"risks":["{fix}"],"required_fixes":["{fix}"],"confidence":0.6}}'
        )

    responses = [
        "initial answer allows data loss",
        request_changes("round 1", "set RPO=0"),
        "revision says RPO=0 but leaves stale text",
        "selfcheck closes RPO=0",
        request_changes("round 2", "remove Retik misspelling"),
        "revision fixes Retek spelling",
        "selfcheck still incomplete",
        request_changes("round 3", "add restore drill evidence"),
    ]

    def fake_call_chat(
        *,
        base_url: str,
        api_key: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        timeout: int,
    ) -> tuple[str, dict[str, object]]:
        calls.append(messages)
        return responses[len(calls) - 1], {"usage": {"total_tokens": 10 + len(calls)}}

    def fake_add_message(run_id: str, speaker: str, model: str, content: str, metadata: dict[str, object]) -> None:
        speakers.append(speaker)

    monkeypatch.setattr(dual_bot_lab, "bothub_config", lambda: {"base_url": "https://example.test/v1", "api_key": "test"})
    monkeypatch.setattr(dual_bot_lab, "run_id", lambda: "dual-max-cycle")
    monkeypatch.setattr(dual_bot_lab, "add_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "call_chat", fake_call_chat)
    monkeypatch.setattr(dual_bot_lab, "add_message", fake_add_message)
    monkeypatch.setattr(dual_bot_lab, "update_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "write_report", lambda **kwargs: tmp_path / "report.md")

    bot1, run_id, verdict, report_path = live_dual_result(
        "Plan SQLite to Postgres migration",
        "Need RPO=0, rollback, and restore drill",
        bot1_model="bot1-model",
        bot2_model="bot2-model",
        max_tokens=100,
        timeout=10,
    )

    assert bot1 == "selfcheck still incomplete"
    assert run_id == "dual-max-cycle"
    assert report_path.endswith("report.md")
    assert verdict["status"] == "REQUEST_CHANGES"
    assert verdict["loop_status"] == "max_review_cycles_reached"
    assert "max_review_cycles_reached" in verdict["risks"]
    assert "Escalate to a human decision after repeated Bot#1/Bot#2 correction cycles." in verdict["required_fixes"]
    assert len(verdict["review_cycles"]) == MAX_BOT_REVIEW_CYCLES
    assert [cycle["bot1_self_check"] for cycle in verdict["review_cycles"]] == [False, True, True]
    assert verdict["review_cycles"][-1]["repair_loop_exhausted"] is True
    assert verdict["review_cycles"][-1]["loop_status"] == "max_review_cycles_reached"
    assert "max_review_cycles_reached" in verdict["review_cycles"][-1]["risks"]
    assert "Escalate to a human decision after repeated Bot#1/Bot#2 correction cycles." in verdict["review_cycles"][-1]["required_fixes"]
    assert "selfcheck closes RPO=0" in calls[4][1]["content"]
    assert "revision says RPO=0 but leaves stale text" not in calls[4][1]["content"]
    assert speakers == [
        "Bot#1",
        "Bot#2-1",
        "Bot#1-revision-2",
        "Bot#1-self-check-2",
        "Bot#2-2",
        "Bot#1-revision-3",
        "Bot#1-self-check-3",
        "Bot#2-3",
    ]


def test_dual_bot_lab_storage_and_report_are_redacted(monkeypatch, tmp_path: Path) -> None:
    store = tmp_path / "dual_bot_lab.db"
    reports = tmp_path / "reports"
    secret = "github_pat_" + "A" * 30

    monkeypatch.setattr(dual_bot_lab, "STORE_PATH", store)
    monkeypatch.setattr(dual_bot_lab, "REPORT_DIR", reports)

    dual_bot_lab.add_run("dual-redact", f"Task with {secret}", f"Acceptance {secret}", "bot1", "bot2")
    dual_bot_lab.add_message(
        "dual-redact",
        "Bot#1",
        "bot1",
        f"Bot#1 saw {secret}",
        {"raw": f"metadata {secret}"},
    )
    report = dual_bot_lab.write_report(
        run_id_value="dual-redact",
        task=f"Task with {secret}",
        acceptance=f"Acceptance {secret}",
        bot1_model="bot1",
        bot1_result=f"Bot#1 transcript {secret}",
        bot2_model="bot2",
        bot2_result=f"Bot#2 transcript {secret}",
    )

    with sqlite3.connect(store) as con:
        run = con.execute("SELECT task, acceptance FROM dual_bot_runs WHERE id='dual-redact'").fetchone()
        message = con.execute("SELECT content, metadata_json FROM dual_bot_messages WHERE run_id='dual-redact'").fetchone()

    assert run is not None
    assert message is not None
    stored_text = "\n".join([run[0], run[1], message[0], message[1]])
    assert secret not in stored_text
    assert "[REDACTED]" in stored_text

    report_text = report.read_text(encoding="utf-8")
    assert secret not in report_text
    assert "[REDACTED]" in report_text


def test_dual_bot_lab_report_falls_back_to_writable_data_dir(monkeypatch, tmp_path: Path) -> None:
    blocked_report_path = tmp_path / "reports-is-a-file"
    blocked_report_path.write_text("not a directory", encoding="utf-8")
    data_reports = tmp_path / "data-reports"

    monkeypatch.setattr(dual_bot_lab, "REPORT_DIR", blocked_report_path)
    monkeypatch.setattr(dual_bot_lab, "DATA_REPORT_DIR", data_reports)
    monkeypatch.setattr(dual_bot_lab, "FALLBACK_REPORT_DIR", tmp_path / "fallback-reports")

    report = dual_bot_lab.write_report(
        run_id_value="dual-fallback",
        task="Task",
        acceptance="Acceptance",
        bot1_model="bot1",
        bot1_result="Bot#1 transcript",
        bot2_model="bot2",
        bot2_result="Bot#2 transcript",
    )

    assert report == data_reports / "dual-fallback.md"
    assert report.exists()
