from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from supervisor_common import (  # noqa: E402
    NO_MEANING,
    YES_MEANING,
    build_acceptance_contract,
    escalation_text,
    parse_bot2_verdict,
    supervisor_status_for_verdict,
)


def test_verdict_status_mapping() -> None:
    assert supervisor_status_for_verdict({"status": "APPROVE"}) == "approved"
    assert supervisor_status_for_verdict({"status": "APPROVE_WITH_EVIDENCE"}) == "approved"
    assert supervisor_status_for_verdict({"status": "REJECT"}) == "awaiting_human_decision"
    assert supervisor_status_for_verdict({"status": "NEEDS_HUMAN"}) == "awaiting_human_decision"
    assert supervisor_status_for_verdict({"status": "REQUEST_CHANGES"}) == "awaiting_human_decision"
    assert supervisor_status_for_verdict({"status": "INSUFFICIENT_EVIDENCE"}) == "awaiting_human_decision"
    assert supervisor_status_for_verdict({"status": "RUBBER_STAMP_RISK"}) == "awaiting_human_decision"
    assert supervisor_status_for_verdict({"status": "BLOCKED_BY_POLICY"}) == "blocked"
    assert supervisor_status_for_verdict({"status": "LOOP_DETECTED"}) == "blocked"
    assert supervisor_status_for_verdict({"status": "BROKEN"}) == "failed"


def test_human_decision_semantics_are_explicit() -> None:
    assert "Bot#2" in YES_MEANING
    assert "Bot#1" in NO_MEANING
    assert "доработку" in YES_MEANING
    assert "как есть" in NO_MEANING


def test_acceptance_contract_contains_gate_rules() -> None:
    contract = build_acceptance_contract("Deploy server code with database migration")
    assert contract["risk_level"] == "high"
    joined = "\n".join(contract["acceptance_criteria"])
    assert "Bot#2" in joined
    assert "user override" in joined
    assert contract["human_decision_semantics"]["yes"] == YES_MEANING
    assert contract["human_decision_semantics"]["no"] == NO_MEANING


def test_escalation_text_contains_both_versions_and_readable_choices() -> None:
    message = escalation_text(
        {"bot1_result": "Bot#1 version: deploy is ready"},
        {
            "status": "REJECT",
            "summary": "Bot#2 version: tests are missing",
            "risks": ["production risk"],
            "required_fixes": ["run smoke test"],
        },
    )

    assert "Сообщение от Bot#2" in message
    assert "Bot#1 version: deploy is ready" in message
    assert "Bot#2 version: tests are missing" in message
    assert "production risk" in message
    assert "run smoke test" in message
    assert "Да —" in message
    assert "Нет —" in message


def test_bot2_verdict_parser_compacts_verbose_fields() -> None:
    long_summary = " ".join(["summary"] * 80)
    long_item = " ".join(["item"] * 80)
    raw = {
        "status": "REQUEST_CHANGES",
        "approved_action": "needs_human",
        "summary": long_summary,
        "evidence_checked": [long_item, long_item, long_item, long_item],
        "risks": [long_item, long_item, long_item, long_item],
        "required_fixes": [long_item, long_item, long_item, long_item],
        "confidence": 0.5,
    }

    verdict = parse_bot2_verdict(json.dumps(raw))

    assert verdict["status"] == "REQUEST_CHANGES"
    assert len(verdict["summary"]) <= 180
    assert len(verdict["evidence_checked"]) == 3
    assert len(verdict["risks"]) == 3
    assert len(verdict["required_fixes"]) == 3
    assert all(len(item) <= 120 for item in verdict["evidence_checked"])
    assert all(len(item) <= 160 for item in verdict["risks"])
    assert all(len(item) <= 180 for item in verdict["required_fixes"])
