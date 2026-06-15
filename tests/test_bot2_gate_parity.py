from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import bot2_gate  # noqa: E402
from sqlite_utils import connect as sqlite_connect  # noqa: E402


def test_bot2_gate_review_repairs_invalid_json_once(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: list[str] = []
    store = tmp_path / "bot2_gate.db"

    def fake_run_hermes(prompt: str, *, toolsets: str = "", timeout: int = 600) -> tuple[int, str]:
        calls.append(prompt)
        if len(calls) == 1:
            return 0, "Looks good, but this is not JSON."
        return (
            0,
            '{"status":"APPROVE","approved_action":"execute","summary":"repair ok",'
            '"evidence_checked":["bot1"],"risks":[],"required_fixes":[],"confidence":0.8}',
        )

    monkeypatch.setattr(bot2_gate, "run_hermes", fake_run_hermes)
    monkeypatch.setattr(bot2_gate, "send_telegram", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot2_gate, "session_id", lambda: "bot2-test")

    sid = bot2_gate.run_review(
        mode="manual",
        task="Change code",
        acceptance="Need tests",
        bot1_result="Bot#1 result",
        evidence="pytest passed",
        toolsets="",
        timeout=10,
        no_telegram=True,
        store_path=store,
    )

    output = json.loads(capsys.readouterr().out)
    assert sid == "bot2-test"
    assert output["session_id"] == "bot2-test"
    assert output["verdict"]["status"] == "APPROVE"
    assert output["verdict"]["repair_attempted"] is True
    assert output["verdict"]["repair_status"] == "repaired"
    assert len(calls) == 2
    assert "Return ONLY valid JSON matching this schema" in calls[1]

    with sqlite_connect(store) as con:
        raw_output = con.execute("SELECT raw_output FROM bot2_verdicts WHERE session_id='bot2-test'").fetchone()[0]
    assert "Bot#2 JSON Repair" in raw_output


def test_bot2_gate_review_fails_closed_when_repair_is_invalid(monkeypatch, tmp_path: Path, capsys) -> None:
    calls: list[str] = []
    store = tmp_path / "bot2_gate.db"

    def fake_run_hermes(prompt: str, *, toolsets: str = "", timeout: int = 600) -> tuple[int, str]:
        calls.append(prompt)
        return 0, "Still not JSON."

    monkeypatch.setattr(bot2_gate, "run_hermes", fake_run_hermes)
    monkeypatch.setattr(bot2_gate, "send_telegram", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot2_gate, "session_id", lambda: "bot2-fail")

    bot2_gate.run_review(
        mode="manual",
        task="Change code",
        acceptance="Need tests",
        bot1_result="Bot#1 result",
        evidence="pytest missing",
        toolsets="",
        timeout=10,
        no_telegram=True,
        store_path=store,
    )

    output = json.loads(capsys.readouterr().out)
    assert output["verdict"]["status"] == "INVALID_BOT2_OUTPUT"
    assert output["verdict"]["repair_attempted"] is True
    assert output["verdict"]["repair_status"] == "failed_closed"
    assert output["verdict"]["confidence"] == 0.0
    assert len(calls) == 2


def test_bot2_gate_storage_stdout_and_events_are_redacted(monkeypatch, tmp_path: Path, capsys) -> None:
    store = tmp_path / "bot2_gate.db"
    secret = "github_pat_" + "B" * 30

    def fake_run_hermes(prompt: str, *, toolsets: str = "", timeout: int = 600) -> tuple[int, str]:
        return (
            0,
            '{"status":"NEEDS_HUMAN","approved_action":"needs_human","summary":"secret '
            + secret
            + '","evidence_checked":["'
            + secret
            + '"],"risks":["'
            + secret
            + '"],"required_fixes":["rotate"],"confidence":0.4}',
        )

    monkeypatch.setattr(bot2_gate, "run_hermes", fake_run_hermes)
    monkeypatch.setattr(bot2_gate, "send_telegram", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot2_gate, "session_id", lambda: "bot2-redact")

    bot2_gate.run_review(
        mode="manual",
        task=f"Task {secret}",
        acceptance=f"Acceptance {secret}",
        bot1_result=f"Bot#1 {secret}",
        evidence=f"Evidence {secret}",
        toolsets="",
        timeout=10,
        no_telegram=True,
        store_path=store,
    )

    stdout = capsys.readouterr().out
    assert secret not in stdout
    assert "[REDACTED]" in stdout

    with sqlite_connect(store) as con:
        session = con.execute("SELECT task, acceptance_criteria, bot1_result, evidence FROM bot2_review_sessions").fetchone()
        rounds = con.execute("SELECT message FROM bot2_review_rounds ORDER BY id").fetchall()
        verdict = con.execute("SELECT verdict_json, raw_output FROM bot2_verdicts").fetchone()
        events = con.execute("SELECT payload_json FROM bot2_events ORDER BY id").fetchall()

    stored = "\n".join(
        [*session, *(row[0] for row in rounds), verdict[0], verdict[1], *(row[0] for row in events)]
    )
    assert secret not in stored
    assert "[REDACTED]" in stored
