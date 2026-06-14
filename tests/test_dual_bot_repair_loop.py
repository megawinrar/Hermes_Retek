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


def test_run_case_counts_request_changes(monkeypatch, tmp_path: Path) -> None:
    calls: list[str] = []

    def fake_call_chat(**kwargs):
        messages = kwargs["messages"]
        joined = "\n".join(item["content"] for item in messages)
        calls.append(joined)
        if "Previous Bot#1 answer" in joined:
            return "revised answer with inverse normalization", {"usage": {"total_tokens": 11}}
        if "Bot#1 result:" in joined and "revised answer" in joined:
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
    assert any("Previous Bot#1 answer" in call for call in calls)
