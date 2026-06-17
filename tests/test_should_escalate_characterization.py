"""Characterization tests for bot2_gate.should_escalate.

IMPORTANT: this file pins the CURRENT behavior, INCLUDING the known drift bug
(BUG-1 in docs/refactoring/03_latent_bugs.md): bot2_gate.should_escalate keeps a
local status list that has diverged from supervisor_common.ESCALATION_STATUSES.

The two statuses NEED_HUMAN_DECISION and REFACTORING_REQUIRED currently do NOT
escalate here. Phase 2 fixes BUG-1 and flips the two assertions in
test_drift_statuses_currently_do_not_escalate to True (before/after evidence).
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from bot2_gate import should_escalate  # noqa: E402


ESCALATING = [
    "REJECT",
    "NEEDS_HUMAN",
    "REQUEST_CHANGES",
    "INSUFFICIENT_EVIDENCE",
    "MISSING_TESTS_FOR_CODE_CHANGE",
    "FAKE_IMPLEMENTATION_DETECTED",
    "TEST_THEATER_DETECTED",
    "RUBBER_STAMP_RISK",
    "INVALID_BOT2_OUTPUT",
]

NON_ESCALATING = [
    "APPROVE",
    "APPROVE_WITH_EVIDENCE",
    "BLOCKED_BY_POLICY",
    "LOOP_DETECTED",
    "SOMETHING_UNKNOWN",
    "",
]


@pytest.mark.parametrize("status", ESCALATING)
def test_escalating_statuses(status: str) -> None:
    assert should_escalate({"status": status}) is True


@pytest.mark.parametrize("status", NON_ESCALATING)
def test_non_escalating_statuses(status: str) -> None:
    assert should_escalate({"status": status}) is False


def test_status_is_case_insensitive() -> None:
    assert should_escalate({"status": "reject"}) is True


@pytest.mark.parametrize("status", ["NEED_HUMAN_DECISION", "REFACTORING_REQUIRED"])
def test_drift_statuses_currently_do_not_escalate(status: str) -> None:
    # BUG-1: these ARE in supervisor_common.ESCALATION_STATUSES but the gate's
    # local list omits them. Phase 2 will flip these to True.
    assert should_escalate({"status": status}) is False
