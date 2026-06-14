from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import parallel_orchestration  # noqa: E402


def args_for_policy(**overrides: object) -> object:
    values: dict[str, object] = {
        "timeout": 0,
        "max_tokens": 100,
        "max_parallel_agents": None,
        "verification_parallel_agents": None,
        "agent_timeout_seconds": None,
        "agent_max_tokens": None,
        "bothub_max_parallel_calls": None,
        "bothub_requests_per_minute": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_env_int_and_arg_int_use_defaults_env_and_minimums(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_MAX_PARALLEL_AGENTS", "7")
    monkeypatch.setenv("HERMES_BAD_INT", "not-an-int")
    monkeypatch.setenv("HERMES_NEGATIVE_INT", "-5")

    assert parallel_orchestration.env_int("HERMES_BAD_INT", 3) == 3
    assert parallel_orchestration.env_int("HERMES_NEGATIVE_INT", 3, minimum=1) == 1
    assert parallel_orchestration.arg_int(args_for_policy(), "max_parallel_agents", 2) == 7
    assert parallel_orchestration.arg_int(args_for_policy(max_parallel_agents="-2"), "max_parallel_agents", 2) == 0
    assert parallel_orchestration.arg_int(args_for_policy(max_parallel_agents="bad"), "max_parallel_agents", 2) == 2


def test_policy_caps_agents_timeout_budget_and_bothub_with_injected_token_policy(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HERMES_AGENT_WORKSPACE_ROOT", str(tmp_path / "agent-workspaces"))
    monkeypatch.setenv("HERMES_BOTHUB_COOLDOWN_MS", "375")
    route = {
        "task_level": "L4",
        "risk_level": "high",
        "needs_agents": True,
        "max_agents": 9,
        "model_policy": {"bot1_model": "gpt-5.3-codex"},
    }
    args = args_for_policy(
        timeout=90,
        max_tokens=2000,
        max_parallel_agents=9,
        verification_parallel_agents=9,
        agent_timeout_seconds=240,
        agent_max_tokens=1800,
        bothub_max_parallel_calls=4,
        bothub_requests_per_minute=18,
    )
    token_budget_calls: list[dict[str, object]] = []

    def token_budget_for_role(requested: int, **kwargs: object) -> int:
        token_budget_calls.append({"requested": requested, **kwargs})
        return 1100

    policy = parallel_orchestration.bounded_parallel_orchestration_policy(
        route,
        process_id_value="proc-test",
        args=args,
        token_policy_level_fn=lambda _route: "L4",
        token_budget_for_role_fn=token_budget_for_role,
    )

    assert policy["enabled"] is True
    assert policy["level"] == "L4"
    assert policy["max_parallel_agents"] == 5
    assert policy["verification_parallel_agents"] == 3
    assert policy["per_agent_timeout_seconds"] == 90
    assert policy["per_agent_token_budget"] == 1100
    assert policy["workspace"]["root"] == str(tmp_path / "agent-workspaces")
    assert policy["workspace"]["template"].endswith("/proc-test/{agent_id}")
    assert policy["bothub_rate_limits"]["max_parallel_calls"] == 4
    assert policy["bothub_rate_limits"]["requests_per_minute"] == 18
    assert policy["bothub_rate_limits"]["cooldown_ms"] == 375
    assert token_budget_calls == [
        {
            "requested": 2000,
            "role": "bot1",
            "route": route,
            "model": "gpt-5.3-codex",
        }
    ]


def test_policy_allows_l2_verification_without_discovery_agents() -> None:
    policy = parallel_orchestration.bounded_parallel_orchestration_policy(
        {
            "task_level": "L2",
            "risk_level": "medium",
            "needs_agents": False,
            "max_agents": 3,
        },
        process_id_value="proc-l2",
        args=args_for_policy(),
    )

    assert policy["enabled"] is True
    assert policy["max_parallel_agents"] == 0
    assert policy["verification_parallel_agents"] == 1
    assert policy["per_agent_token_budget"] == 0
    assert policy["enabled_phases"] == ["verification"]
    assert policy["bothub_rate_limits"]["max_parallel_calls"] == 1
