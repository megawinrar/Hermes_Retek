from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_bot_lab  # noqa: E402
from process_orchestrator import live_dual_result  # noqa: E402


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
    assert len(calls) == 3
    assert "Return ONLY valid JSON matching this schema" in calls[2][1]["content"]
    assert [speaker for speaker, _content in messages] == ["Bot#1", "Bot#2", "Bot#2-repair"]


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
    assert len(calls) == 3


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
