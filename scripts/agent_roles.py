#!/usr/bin/env python3
"""Machine-readable Hermes agent role contracts."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AgentRole:
    name: str
    owner: str
    phase: str
    writes_shared_state: bool
    requires_tool_gateway: bool
    max_parallel: int
    summary: str


ROLE_CONTRACTS: tuple[AgentRole, ...] = (
    AgentRole(
        name="architect",
        owner="contracts_and_rollout",
        phase="planning",
        writes_shared_state=True,
        requires_tool_gateway=True,
        max_parallel=1,
        summary="Owns ADRs, rollout order, acceptance criteria, and final merge decisions.",
    ),
    AgentRole(
        name="security_vault_agent",
        owner="secret_intake_and_redaction",
        phase="discovery",
        writes_shared_state=False,
        requires_tool_gateway=True,
        max_parallel=1,
        summary="Stores secrets as vault references and prevents raw secret values from entering logs or prompts.",
    ),
    AgentRole(
        name="context_engineer",
        owner="context_budget_and_compaction",
        phase="discovery",
        writes_shared_state=False,
        requires_tool_gateway=False,
        max_parallel=1,
        summary="Tracks context usage and emits compaction checkpoints before context pressure becomes risky.",
    ),
    AgentRole(
        name="orchestrator_engineer",
        owner="parallelism_and_workspaces",
        phase="discovery",
        writes_shared_state=False,
        requires_tool_gateway=True,
        max_parallel=1,
        summary="Owns bounded parallelism, isolated agent workspaces, and single-writer state discipline.",
    ),
    AgentRole(
        name="bot1_developer",
        owner="implementation",
        phase="execution",
        writes_shared_state=False,
        requires_tool_gateway=True,
        max_parallel=1,
        summary="Implements approved scoped changes in an isolated workspace.",
    ),
    AgentRole(
        name="tester",
        owner="verification_evidence",
        phase="verification",
        writes_shared_state=False,
        requires_tool_gateway=False,
        max_parallel=3,
        summary="Runs focused tests, records evidence, and checks rollback assumptions.",
    ),
    AgentRole(
        name="bot2_reviewer",
        owner="independent_review",
        phase="verification",
        writes_shared_state=False,
        requires_tool_gateway=False,
        max_parallel=1,
        summary="Checks plans, patches, evidence, and risk using machine-readable verdicts.",
    ),
    AgentRole(
        name="devops_operator",
        owner="deploy_and_restart",
        phase="execution",
        writes_shared_state=True,
        requires_tool_gateway=True,
        max_parallel=1,
        summary="Performs server rollout, cleanup, and restart only through safety gates.",
    ),
)


def roles_as_dicts() -> list[dict[str, Any]]:
    return [asdict(role) for role in ROLE_CONTRACTS]


def role_by_name(name: str) -> dict[str, Any]:
    normalized = name.strip().lower()
    for role in ROLE_CONTRACTS:
        if role.name == normalized:
            return asdict(role)
    raise KeyError(f"unknown role: {name}")


def roles_for_phase(phase: str) -> list[dict[str, Any]]:
    normalized = phase.strip().lower()
    return [asdict(role) for role in ROLE_CONTRACTS if role.phase == normalized]


def role_runtime_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "single_writer_roles": [role.name for role in ROLE_CONTRACTS if role.writes_shared_state],
        "tool_gateway_roles": [role.name for role in ROLE_CONTRACTS if role.requires_tool_gateway],
        "parallel_roles": {role.name: role.max_parallel for role in ROLE_CONTRACTS},
        "roles": roles_as_dicts(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print Hermes agent role contracts.")
    parser.add_argument("--phase", default="", help="Filter roles by phase.")
    parser.add_argument("--role", default="", help="Print one role by name.")
    args = parser.parse_args(argv)

    if args.role:
        payload: Any = role_by_name(args.role)
    elif args.phase:
        payload = roles_for_phase(args.phase)
    else:
        payload = role_runtime_contract()
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
