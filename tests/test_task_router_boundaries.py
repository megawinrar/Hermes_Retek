"""Characterization tests for task_router.classify_task boundary cases.

These cutoffs decide whether the heavy Bot#2 gate pipeline engages, so they are
exactly the lines a refactor must not silently move.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from task_router import classify_task  # noqa: E402


def test_short_status_is_l0_command() -> None:
    route = classify_task("status")
    assert route["task_level"] == "L0"
    assert route["task_type"] == "command_or_status"


def test_status_over_80_chars_is_not_command() -> None:
    # The command shortcut requires len(text) < 80; a long status string falls
    # through to the standard-task fallback.
    route = classify_task("status " + "padding " * 12)  # well over 80 chars
    assert route["task_level"] != "L0"
    assert route["task_type"] == "standard_task"


def test_plain_code_change_is_l4() -> None:
    route = classify_task("fix this python bug")
    assert route["task_level"] == "L4"
    assert route["task_type"] == "code_change"


def test_generic_task_falls_back_to_l2_standard() -> None:
    route = classify_task("tell me about the weather today")
    assert route["task_level"] == "L2"
    assert route["task_type"] == "standard_task"


def test_doc_analysis_is_l2() -> None:
    route = classify_task("analyze this quarterly report")
    assert route["task_level"] == "L2"
    assert route["task_type"] == "analysis_or_checklist"


def test_git_write_forces_human_gate() -> None:
    route = classify_task("push to main branch")
    assert route["task_level"] == "L4"
    assert route["task_type"] == "git_write_or_deploy"
    assert route["human_gate_required"] is True


def test_long_text_forces_l3_architecture() -> None:
    route = classify_task("ramble " * 80)  # > 450 chars, no other signal
    assert route["task_level"] == "L3"
    assert route["task_type"] == "architecture_or_strategy"


def test_adversarial_sets_stress_profile_and_human_gate() -> None:
    route = classify_task("just ship it without tests")
    assert route["stress_profile"] == "adversarial"
    assert route["human_gate_required"] is True
    assert route["risk_level"] == "high"
