from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_bot_repair_loop as repair_loop  # noqa: E402


def test_extract_verdict_from_markdown_json_fence() -> None:
    raw = """
## Bot#2 Review

Needs one fix.

```json
{
  "status": "REQUEST_CHANGES",
  "approved_action": "needs_human",
  "summary": "Fix normalization.",
  "evidence_checked": ["answer"],
  "risks": ["wrong ranking"],
  "required_fixes": ["Use inverse normalization"],
  "confidence": 0.82
}
```
"""

    verdict = repair_loop.extract_verdict(raw)

    assert verdict["status"] == "REQUEST_CHANGES"
    assert verdict["required_fixes"] == ["Use inverse normalization"]


def test_bot1_revision_messages_send_only_supervisor_fix_package() -> None:
    messages = repair_loop.bot1_revision_messages(
        task="CRM Ретек supplier scoring",
        acceptance="Need inverse normalization",
        previous_answer="old answer",
        bot2_verdict={
            "summary": "Formula direction is wrong",
            "required_fixes": ["Use score = 1 + (max - x)/(max - min)*4"],
            "risks": ["wrong supplier ranking"],
        },
        round_no=1,
    )

    assert messages[0]["role"] == "system"
    assert "Supervisor package" in messages[0]["content"]
    assert "Do not substitute \"Ретейл\"" in messages[0]["content"]
    assert "Formula direction is wrong" in messages[1]["content"]
    assert "Use score = 1 + (max - x)/(max - min)*4" in messages[1]["content"]
    assert "## What I Changed From Bot#2 Feedback" in messages[1]["content"]


def test_bot1_self_check_messages_require_closing_each_fix() -> None:
    messages = repair_loop.bot1_self_check_messages(
        task="SQLite to Postgres migration",
        acceptance="Need RPO=0",
        draft_answer="Rollback may lose new data after migration starts.",
        bot2_verdict={
            "summary": "RPO contradiction",
            "required_fixes": ["Rewrite cutover/rollback to RPO=0"],
            "risks": ["data loss"],
        },
        round_no=2,
    )

    assert messages[0]["role"] == "system"
    assert "self-consistency gate" in messages[0]["content"]
    assert "RPO=0" in messages[0]["content"]
    assert "Rewrite cutover/rollback to RPO=0" in messages[1]["content"]
    assert "every required fix is explicitly closed" in messages[1]["content"]
    assert "## Self-Consistency Checklist" in messages[1]["content"]


def test_run_case_counts_request_changes(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_call_chat(**kwargs):
        messages = kwargs["messages"]
        joined = "\n".join(item["content"] for item in messages)
        calls.append(joined)
        if "Bot#1 draft answer to self-check" in joined:
            return "self-checked revised answer with inverse normalization", {"usage": {"total_tokens": 12}}
        if "Previous Bot#1 answer" in joined:
            return "revised answer with inverse normalization", {"usage": {"total_tokens": 11}}
        if "Bot#1 result:" in joined and "self-checked revised answer" in joined:
            return (
                '{"status":"APPROVE","approved_action":"execute","summary":"ok",'
                '"evidence_checked":["revision"],"risks":[],"required_fixes":[],"confidence":0.9}',
                {"usage": {"total_tokens": 13}},
            )
        if "Bot#1 result:" in joined:
            return (
                '{"status":"REQUEST_CHANGES","approved_action":"needs_human","summary":"fix formula",'
                '"evidence_checked":["initial"],"risks":["wrong direction"],'
                '"required_fixes":["Use inverse normalization"],"confidence":0.8}',
                {"usage": {"total_tokens": 13}},
            )
        return "initial answer with wrong direct normalization", {"usage": {"total_tokens": 10}}

    monkeypatch.setattr(repair_loop.lab, "call_chat", fake_call_chat)
    monkeypatch.setattr(repair_loop.lab, "add_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(repair_loop.lab, "add_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(repair_loop.lab, "run_id", lambda: "dual-test")
    monkeypatch.setattr(repair_loop, "print_block", lambda *args, **kwargs: None)
    monkeypatch.setattr(repair_loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(repair_loop.lab, "REPORT_DIR", tmp_path)

    result = repair_loop.run_case(
        repair_loop.CASES[1],
        cfg={"base_url": "https://example.test/v1", "api_key": "test"},
        bot1_model="deepseek-v4-flash",
        bot2_model="gpt-5.3-codex",
        max_rounds=3,
        max_tokens=100,
        timeout=10,
        pause=0,
        preview_chars=500,
    )

    assert result["final_status"] == "APPROVE"
    assert result["correction_count"] == 1
    assert len(result["turns"]) == 2
    assert result["turns"][1]["bot1_self_check"] == "self-checked revised answer with inverse normalization"
    assert any("Previous Bot#1 answer" in call for call in calls)
    assert any("Bot#1 draft answer to self-check" in call for call in calls)


def test_run_case_repairs_invalid_bot2_json(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []
    speakers: list[str] = []

    def fake_call_chat(**kwargs):
        messages = kwargs["messages"]
        joined = "\n".join(item["content"] for item in messages)
        calls.append(joined)
        if "Return ONLY valid JSON matching this schema" in joined:
            return (
                '{"status":"APPROVE_WITH_EVIDENCE","approved_action":"execute",'
                '"summary":"repaired verdict ok","evidence_checked":["bot1"],'
                '"risks":[],"required_fixes":[],"confidence":0.91}',
                {"usage": {"total_tokens": 15}},
            )
        if "Bot#1 result:" in joined:
            return "not valid json", {"usage": {"total_tokens": 12}}
        return "migration plan", {"usage": {"total_tokens": 10}}

    def fake_add_message(_run_id: str, speaker: str, *_args, **_kwargs) -> None:
        speakers.append(speaker)

    monkeypatch.setattr(repair_loop.lab, "call_chat", fake_call_chat)
    monkeypatch.setattr(repair_loop.lab, "add_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(repair_loop.lab, "add_message", fake_add_message)
    monkeypatch.setattr(repair_loop.lab, "run_id", lambda: "dual-test")
    monkeypatch.setattr(repair_loop, "print_block", lambda *args, **kwargs: None)
    monkeypatch.setattr(repair_loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(repair_loop.lab, "REPORT_DIR", tmp_path)

    result = repair_loop.run_case(
        repair_loop.CASES[2],
        cfg={"base_url": "https://example.test/v1", "api_key": "test"},
        bot1_model="deepseek-v4-flash",
        bot2_model="gpt-5.3-codex",
        max_rounds=3,
        max_tokens=100,
        timeout=10,
        pause=0,
        preview_chars=500,
    )

    assert result["final_status"] == "APPROVE_WITH_EVIDENCE"
    assert result["turns"][0]["verdict"]["repair_attempted"] is True
    assert result["turns"][0]["verdict"]["repair_status"] == "repaired"
    assert any("Return ONLY valid JSON matching this schema" in call for call in calls)
    assert speakers == ["Bot#1 round 1", "Bot#2 round 1", "Bot#2 JSON repair round 1"]
