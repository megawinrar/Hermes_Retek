"""Bounded parallel orchestration policy helpers."""

from __future__ import annotations

import argparse
import os
from typing import Any, Callable


PARALLEL_AGENT_LEVEL_LIMITS: dict[str, dict[str, int]] = {
    "L0": {"max_parallel_agents": 0, "verification_parallel_agents": 0, "agent_timeout_seconds": 0, "agent_max_tokens": 0},
    "L1": {"max_parallel_agents": 0, "verification_parallel_agents": 0, "agent_timeout_seconds": 0, "agent_max_tokens": 0},
    "L2": {"max_parallel_agents": 0, "verification_parallel_agents": 1, "agent_timeout_seconds": 60, "agent_max_tokens": 700},
    "L3": {"max_parallel_agents": 3, "verification_parallel_agents": 3, "agent_timeout_seconds": 120, "agent_max_tokens": 900},
    "L4": {"max_parallel_agents": 5, "verification_parallel_agents": 3, "agent_timeout_seconds": 150, "agent_max_tokens": 1200},
}
BOTHUB_RATE_LIMIT_DEFAULTS = {
    "max_parallel_calls": 2,
    "requests_per_minute": 12,
    "cooldown_ms": 250,
}
AGENT_WORKSPACE_ROOT = os.environ.get("HERMES_AGENT_WORKSPACE_ROOT", "/opt/data/agent_workspaces")


TokenPolicyLevelFn = Callable[[dict[str, Any] | None], str]
TokenBudgetForRoleFn = Callable[..., int]


def env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def arg_int(args: argparse.Namespace, name: str, default: int, *, minimum: int = 0) -> int:
    value = getattr(args, name, None)
    if value is None:
        return env_int(f"HERMES_{name.upper()}", default, minimum=minimum)
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _default_token_policy_level(route: dict[str, Any] | None = None) -> str:
    route = route or {}
    level = str(route.get("task_level") or "L3").upper()
    if level not in PARALLEL_AGENT_LEVEL_LIMITS:
        level = "L3"
    if bool(route.get("human_gate_required")):
        return "L4"
    return level


def _default_token_budget_for_role(
    requested: int,
    *,
    role: str,
    route: dict[str, Any] | None = None,
    model: str = "",
) -> int:
    del role, route, model
    return max(1, int(requested))


def bounded_parallel_orchestration_policy(
    route: dict[str, Any],
    *,
    process_id_value: str,
    args: argparse.Namespace,
    token_policy_level_fn: TokenPolicyLevelFn = _default_token_policy_level,
    token_budget_for_role_fn: TokenBudgetForRoleFn = _default_token_budget_for_role,
) -> dict[str, Any]:
    level = token_policy_level_fn(route)
    profile = PARALLEL_AGENT_LEVEL_LIMITS.get(level, PARALLEL_AGENT_LEVEL_LIMITS["L3"])
    route_max_agents = int(route.get("max_agents") or 0)
    if not bool(route.get("needs_agents")):
        route_max_agents = 0
    configured_max = arg_int(args, "max_parallel_agents", profile["max_parallel_agents"])
    max_parallel_agents = max(0, min(profile["max_parallel_agents"], route_max_agents, configured_max))

    process_timeout = max(0, int(getattr(args, "timeout", 0) or 0))
    configured_timeout = arg_int(args, "agent_timeout_seconds", profile["agent_timeout_seconds"])
    if process_timeout > 0 and configured_timeout > 0:
        per_agent_timeout = min(configured_timeout, process_timeout)
    else:
        per_agent_timeout = configured_timeout

    configured_tokens = arg_int(args, "agent_max_tokens", profile["agent_max_tokens"], minimum=0)
    profile_tokens = int(profile["agent_max_tokens"] or 0)
    policy_token_cap = token_budget_for_role_fn(
        max(1, int(getattr(args, "max_tokens", configured_tokens or 1) or configured_tokens or 1)),
        role="bot1",
        route=route,
        model=(route.get("model_policy") or {}).get("bot1_model", ""),
    )
    per_agent_budget = (
        0
        if max_parallel_agents == 0
        else max(1, min(configured_tokens or profile_tokens, profile_tokens or policy_token_cap, policy_token_cap))
    )

    verification_configured = arg_int(args, "verification_parallel_agents", profile["verification_parallel_agents"])
    verification_parallel_agents = max(0, min(profile["verification_parallel_agents"], verification_configured, max(max_parallel_agents, 1)))
    if max_parallel_agents == 0 and level not in {"L2"}:
        verification_parallel_agents = 0

    bothub_configured_parallel = arg_int(
        args,
        "bothub_max_parallel_calls",
        env_int("HERMES_BOTHUB_MAX_PARALLEL_CALLS", BOTHUB_RATE_LIMIT_DEFAULTS["max_parallel_calls"]),
        minimum=1,
    )
    bothub_max_parallel = min(max(max_parallel_agents, 1), bothub_configured_parallel)
    if max_parallel_agents == 0:
        bothub_max_parallel = 1
    bothub_rpm = arg_int(
        args,
        "bothub_requests_per_minute",
        env_int("HERMES_BOTHUB_REQUESTS_PER_MINUTE", BOTHUB_RATE_LIMIT_DEFAULTS["requests_per_minute"]),
        minimum=1,
    )
    bothub_cooldown_ms = env_int("HERMES_BOTHUB_COOLDOWN_MS", BOTHUB_RATE_LIMIT_DEFAULTS["cooldown_ms"])

    workspace_root = str(os.environ.get("HERMES_AGENT_WORKSPACE_ROOT", AGENT_WORKSPACE_ROOT)).rstrip("/")
    workspace_template = f"{workspace_root}/{process_id_value}/{{agent_id}}"
    enabled_phases = ["discovery", "verification"] if max_parallel_agents > 0 else []
    if max_parallel_agents == 0 and verification_parallel_agents > 0:
        enabled_phases = ["verification"]

    return {
        "version": 1,
        "enabled": bool(max_parallel_agents > 0 or verification_parallel_agents > 0),
        "level": level,
        "max_parallel_agents": max_parallel_agents,
        "verification_parallel_agents": verification_parallel_agents,
        "per_agent_timeout_seconds": per_agent_timeout,
        "per_agent_token_budget": per_agent_budget,
        "enabled_phases": enabled_phases,
        "disabled_phases": ["execution", "approval", "state_transition"],
        "workspace": {
            "mode": "isolated_copy_on_write",
            "root": workspace_root,
            "template": workspace_template,
            "merge_owner": "supervisor",
            "agent_writes_to_shared_workspace": False,
        },
        "state": {
            "single_writer": "supervisor",
            "sqlite_single_writer": True,
            "agent_state_writes_allowed": False,
            "write_queue": "supervisor_tool_gateway",
            "lock_scope": ["process_id", "target_resource"],
        },
        "bothub_rate_limits": {
            "max_parallel_calls": bothub_max_parallel,
            "requests_per_minute": bothub_rpm,
            "cooldown_ms": bothub_cooldown_ms,
            "per_process_llm_call_budget": max(1, max_parallel_agents + verification_parallel_agents + 2),
        },
        "tool_gateway": {
            "required_before_tool_call": True,
            "blocks_parallel_writes": True,
            "deduplicates_evidence_calls": True,
        },
    }
