"""Tests for supervisor_common.build_acceptance_contract (top-15 #14).

Pins the risk-level logic, including the documented quirk that the <40-char
length check runs LAST and demotes a short high-risk TZ to "low".
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from supervisor_common import build_acceptance_contract  # noqa: E402


def test_long_high_risk_tz_is_high() -> None:
    tz = "Deploy the new production server build with rollback and tests included now"
    contract = build_acceptance_contract(tz)
    assert contract["risk_level"] == "high"


def test_long_neutral_tz_is_medium() -> None:
    tz = "Write some documentation describing how the reporting module is organized"
    contract = build_acceptance_contract(tz)
    assert contract["risk_level"] == "medium"


def test_short_tz_is_low_even_with_high_risk_keyword() -> None:
    # Quirk: len(tz) < 40 runs last, so "deploy now" (high-risk word) downgrades
    # to low. Pinned so a refactor cannot silently change it.
    contract = build_acceptance_contract("deploy now")
    assert contract["risk_level"] == "low"


def test_contract_carries_human_decision_semantics() -> None:
    contract = build_acceptance_contract("Compare supplier prices and delivery deadlines for CRM")
    assert set(contract["human_decision_semantics"]) == {"yes", "no"}
    assert contract["tz"].startswith("Compare supplier prices")
