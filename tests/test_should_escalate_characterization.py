"""Tests for bot2_gate.should_escalate.

After the BUG-1 fix (docs/refactoring/03_latent_bugs.md), should_escalate is
coupled to the single canonical supervisor_common.ESCALATION_STATUSES set plus
INVALID_BOT2_OUTPUT. These tests assert that coupling so the local list can
never drift again.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from bot2_gate import should_escalate  # noqa: E402
from supervisor_common import (  # noqa: E402
    APPROVED_STATUSES,
    BLOCKED_STATUSES,
    ESCALATION_STATUSES,
    INVALID_BOT2_STATUS,
)


NON_ESCALATING = sorted(APPROVED_STATUSES | BLOCKED_STATUSES | {"SOMETHING_UNKNOWN", ""})


@pytest.mark.parametrize("status", sorted(ESCALATION_STATUSES))
def test_every_canonical_escalation_status_escalates(status: str) -> None:
    assert should_escalate({"status": status}) is True


def test_invalid_bot2_output_escalates() -> None:
    assert should_escalate({"status": INVALID_BOT2_STATUS}) is True


@pytest.mark.parametrize("status", NON_ESCALATING)
def test_non_escalating_statuses(status: str) -> None:
    assert should_escalate({"status": status}) is False


def test_status_is_case_insensitive() -> None:
    assert should_escalate({"status": "reject"}) is True


@pytest.mark.parametrize("status", ["NEED_HUMAN_DECISION", "REFACTORING_REQUIRED"])
def test_drift_statuses_now_escalate_after_bug1_fix(status: str) -> None:
    # BUG-1 before/after evidence: these are in ESCALATION_STATUSES but the old
    # local list omitted them, so they used to return False. Now they escalate.
    assert should_escalate({"status": status}) is True
