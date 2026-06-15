from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import runtime_guardrails  # noqa: E402


def test_apply_runtime_guardrails_bounds_loops_delegation_and_streaming() -> None:
    config = {
        "model": {"api_key": "secret-value"},
        "agent": {"max_turns": 90, "gateway_notify_interval": 180},
        "delegation": {"max_concurrent_children": 3, "child_timeout_seconds": 600, "max_iterations": 50},
        "streaming": {"enabled": False, "transport": "auto"},
    }

    updated = runtime_guardrails.apply_runtime_guardrails(config)

    assert updated["agent"]["max_turns"] == 16
    assert updated["agent"]["gateway_notify_interval"] == 30
    assert updated["agent"]["gateway_timeout_warning"] == 300
    assert updated["delegation"]["max_concurrent_children"] == 2
    assert updated["delegation"]["child_timeout_seconds"] == 120
    assert updated["delegation"]["max_iterations"] == 8
    assert updated["streaming"]["enabled"] is True
    assert updated["streaming"]["buffer_threshold"] == 24
    assert updated["model"]["api_key"] == "secret-value"


def test_apply_runtime_guardrails_does_not_mutate_input() -> None:
    config = {"agent": {"max_turns": 90}}

    updated = runtime_guardrails.apply_runtime_guardrails(config)

    assert config["agent"]["max_turns"] == 90
    assert updated["agent"]["max_turns"] == 16


def test_apply_runtime_guardrails_recovers_non_mapping_sections() -> None:
    updated = runtime_guardrails.apply_runtime_guardrails(
        {
            "agent": "bad",
            "delegation": None,
            "streaming": [],
        }
    )

    assert updated["agent"]["max_turns"] == 16
    assert updated["delegation"]["child_timeout_seconds"] == 120
    assert updated["streaming"]["enabled"] is True
