#!/usr/bin/env python3
"""Durable startup context packs for fresh Bot1/Bot2 sessions."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import agent_workspace
import rlm_store

try:
    from secret_patterns import redact_payload
except ImportError:  # pragma: no cover - package-style import fallback
    from scripts.secret_patterns import redact_payload


SESSION_STRATEGY = "fresh_session_with_durable_context_pack"


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


BIG_TASK_TYPES = {
    "code_or_deploy_project",
    "database_migration_change",
    "git_write_or_deploy",
    "supplier_price_deadline_analysis",
}
BIG_TASK_KEYWORDS = ("kontur", "контур", "zakupki", "закуп", "excel", "эксель", "deploy", "migration", "миграц")
DEFAULT_CONTEXT_RATIO = _env_float("HERMES_CONTEXT_PACK_RATIO", 0.50)
EXPANDED_CONTEXT_RATIO = _env_float("HERMES_CONTEXT_PACK_EXPANDED_RATIO", 0.70)
DEFAULT_CONTEXT_TOKEN_BUDGET = _env_int("HERMES_CONTEXT_PACK_DEFAULT_TOKENS", 3000, minimum=1)
MIN_CONTEXT_TOKEN_BUDGET = _env_int("HERMES_CONTEXT_PACK_MIN_TOKENS", 120, minimum=1)
MAX_CONTEXT_TOKEN_BUDGET = _env_int("HERMES_CONTEXT_PACK_MAX_TOKENS", 3000, minimum=MIN_CONTEXT_TOKEN_BUDGET)
EXPANDED_MAX_CONTEXT_TOKEN_BUDGET = _env_int("HERMES_CONTEXT_PACK_EXPANDED_MAX_TOKENS", 5000, minimum=MAX_CONTEXT_TOKEN_BUDGET)
BIG_TASK_CHAR_THRESHOLD = _env_int("HERMES_CONTEXT_PACK_BIG_TASK_CHARS", 4000, minimum=1)
PREVIEW_CHARS = 900


def truncate_text(value: str, *, limit: int = PREVIEW_CHARS) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 18)].rstrip() + "\n...[truncated]"


def startup_context_token_budget(
    max_tokens: int | None = None,
    *,
    ratio: float = DEFAULT_CONTEXT_RATIO,
    max_budget: int = MAX_CONTEXT_TOKEN_BUDGET,
) -> int:
    if not max_tokens or int(max_tokens) <= 0:
        return DEFAULT_CONTEXT_TOKEN_BUDGET
    budget = int(int(max_tokens) * ratio)
    return max(MIN_CONTEXT_TOKEN_BUDGET, min(max_budget, budget))


def expanded_context_required(
    *,
    route: dict[str, Any] | None = None,
    phase: str = "initial",
    task: str = "",
    acceptance: str = "",
) -> bool:
    route = route or {}
    phase_value = str(phase or "initial")
    task_type = str(route.get("task_type") or "")
    task_level = str(route.get("task_level") or "").upper()
    combined = f"{task}\n{acceptance}".lower()
    if phase_value not in {"", "initial"}:
        return True
    if task_level == "L4" or bool(route.get("human_gate_required")):
        return True
    if task_type in BIG_TASK_TYPES:
        return True
    if bool(route.get("needs_agents")) and bool(route.get("review_required")) and str(route.get("risk_level")) == "high":
        return True
    if len(combined) >= BIG_TASK_CHAR_THRESHOLD:
        return True
    return any(keyword in combined for keyword in BIG_TASK_KEYWORDS)


def startup_context_token_budget_for_route(
    max_tokens: int | None = None,
    *,
    route: dict[str, Any] | None = None,
    phase: str = "initial",
    task: str = "",
    acceptance: str = "",
) -> int:
    if expanded_context_required(route=route, phase=phase, task=task, acceptance=acceptance):
        return startup_context_token_budget(
            max_tokens,
            ratio=EXPANDED_CONTEXT_RATIO,
            max_budget=EXPANDED_MAX_CONTEXT_TOKEN_BUDGET,
        )
    return startup_context_token_budget(max_tokens)


def attach_role_context_packs(
    skill_context: dict[str, Any],
    role_context_packs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    updated = copy.deepcopy(skill_context)
    if role_context_packs:
        updated["role_context_packs"] = redact_payload(role_context_packs)
    return updated


def _route_summary(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_level": route.get("task_level", ""),
        "task_type": route.get("task_type", ""),
        "risk_level": route.get("risk_level", ""),
        "review_required": bool(route.get("review_required")),
        "human_gate_required": bool(route.get("human_gate_required")),
        "process_plan": list(route.get("process_plan") or []),
        "needs_agents": bool(route.get("needs_agents")),
    }


def _role_skill_summary(skill_context: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "role": role,
        "task_tags": list(skill_context.get("task_tags") or []),
        "skills": copy.deepcopy((skill_context.get("roles") or {}).get(role, [])),
        "gated_skills": copy.deepcopy((skill_context.get("gated_roles") or {}).get(role, [])),
        "runtime_contract": copy.deepcopy(skill_context.get("runtime_contract") or {}),
        "tool_results": copy.deepcopy(skill_context.get("tool_results") or []),
    }


def _workspace_snapshot(
    *,
    process_id: str,
    role: str,
    workspace_root: str | None = None,
) -> dict[str, Any]:
    try:
        status = agent_workspace.workspace_status(process_id, role, workspace_root)
    except Exception as exc:
        return {
            "agent_id": role,
            "exists": False,
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }
    metadata = status.get("metadata") or {}
    return {
        "agent_id": role,
        "path": status.get("path", ""),
        "exists": bool(status.get("exists")),
        "status": metadata.get("status", "missing" if not status.get("exists") else ""),
        "mode": metadata.get("mode", agent_workspace.WORKSPACE_MODE if not status.get("exists") else ""),
        "merge_owner": metadata.get("merge_owner", "supervisor"),
        "auto_merge": bool(metadata.get("auto_merge", False)),
    }


def _latest_verdict(events: list[dict[str, Any]], assignments: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("event_type") == "bot2_verdict":
            verdict = (event.get("payload") or {}).get("verdict")
            if isinstance(verdict, dict):
                return verdict
    for assignment in reversed(assignments):
        if assignment.get("worker") == "bot2":
            verdict = ((assignment.get("output") or {}).get("verdict")) or {}
            if isinstance(verdict, dict):
                return verdict
    return {}


def _verdict_from_inputs(
    *,
    prior_verdict: dict[str, Any] | None,
    events: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
) -> dict[str, Any]:
    if isinstance(prior_verdict, dict) and prior_verdict:
        return prior_verdict
    return _latest_verdict(events, assignments)


def _verdict_string_list(
    *,
    prior_verdict: dict[str, Any] | None,
    events: list[dict[str, Any]],
    assignments: list[dict[str, Any]],
    key: str,
) -> list[str]:
    verdict = _verdict_from_inputs(prior_verdict=prior_verdict, events=events, assignments=assignments)
    values = verdict.get(key) if isinstance(verdict, dict) else []
    return [str(item) for item in (values or [])]


def _previous_attempts(assignments: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    for assignment in reversed(assignments):
        worker = str(assignment.get("worker") or "")
        if worker not in {"bot1", "bot2", "tester"}:
            continue
        output = assignment.get("output") or {}
        verdict = output.get("verdict") if isinstance(output, dict) else {}
        item: dict[str, Any] = {
            "worker": worker,
            "phase": assignment.get("phase", ""),
            "status": assignment.get("status", ""),
            "created_at": assignment.get("created_at", ""),
        }
        if isinstance(output, dict):
            for key in ("mode", "result_chars", "evidence_chars", "report_path", "review_cycle_count", "source"):
                if key in output:
                    item[key] = output.get(key)
        if isinstance(verdict, dict) and verdict:
            item["verdict"] = {
                "status": verdict.get("status", ""),
                "summary": verdict.get("summary", ""),
                "required_fixes": verdict.get("required_fixes", []),
                "risks": verdict.get("risks", []),
            }
        attempts.append(item)
        if len(attempts) >= limit:
            break
    return list(reversed(attempts))


def _human_decision(events: list[dict[str, Any]], supervisor_state: dict[str, Any] | None) -> dict[str, Any]:
    supervisor_state = supervisor_state or {}
    escalations = supervisor_state.get("human_escalations") or []
    if escalations:
        latest = escalations[-1] or {}
        return {
            "status": latest.get("status", ""),
            "choice": latest.get("choice", ""),
            "meaning": latest.get("meaning", ""),
            "reason": latest.get("reason", ""),
            "bot2_session_id": latest.get("bot2_session_id", ""),
        }
    for event in reversed(events):
        if event.get("event_type") == "human_decision":
            decision = (event.get("payload") or {}).get("decision") or {}
            if isinstance(decision, dict):
                return {
                    "status": decision.get("status", ""),
                    "choice": decision.get("choice", ""),
                    "meaning": decision.get("meaning", ""),
                    "reason": decision.get("reason", ""),
                    "bot2_session_id": decision.get("bot2_session_id", ""),
                }
    return {}


def _empty_rlm_pack(token_budget: int) -> dict[str, Any]:
    return {"token_budget": int(token_budget), "estimated_tokens": 0, "context": "", "records": []}


def _merge_rlm_packs(packs: list[dict[str, Any]], *, token_budget: int) -> dict[str, Any]:
    selected_records: list[dict[str, Any]] = []
    selected_lines: list[str] = []
    seen: set[int] = set()
    max_chars = max(0, int(token_budget) * 4)

    for pack in packs:
        lines = str(pack.get("context") or "").splitlines()
        records = list(pack.get("records") or [])
        for index, record in enumerate(records):
            record_id = int(record.get("id") or 0)
            if record_id in seen:
                continue
            line = lines[index] if index < len(lines) else ""
            if not line:
                continue
            candidate = "\n".join([*selected_lines, line]) if selected_lines else line
            if len(candidate) > max_chars or rlm_store.estimate_tokens(candidate) > token_budget:
                continue
            seen.add(record_id)
            selected_records.append(record)
            selected_lines.append(line)

    context = "\n".join(selected_lines)
    return {
        "token_budget": int(token_budget),
        "estimated_tokens": rlm_store.estimate_tokens(context),
        "context": context[:max_chars],
        "records": selected_records,
    }


def _rlm_context_pack(
    *,
    process_id: str,
    route: dict[str, Any],
    skill_context: dict[str, Any],
    token_budget: int,
    rlm_store_path: str | Path | None = None,
    rlm_enabled: bool = False,
) -> dict[str, Any]:
    if not rlm_enabled and not rlm_store_path:
        return _empty_rlm_pack(token_budget)

    packs: list[dict[str, Any]] = []
    try:
        packs.append(
            rlm_store.build_context_pack(
                process_id=process_id,
                token_budget=max(40, token_budget // 2),
                store_path=rlm_store_path,
            )
        )
        task_type = str(route.get("task_type") or "")
        if task_type:
            packs.append(
                rlm_store.build_context_pack(
                    tags=[f"type/{task_type}"],
                    token_budget=max(40, token_budget // 3),
                    store_path=rlm_store_path,
                )
            )
        for tag in list(skill_context.get("task_tags") or [])[:2]:
            packs.append(
                rlm_store.build_context_pack(
                    tags=[f"task/{tag}"],
                    token_budget=max(40, token_budget // 4),
                    store_path=rlm_store_path,
                )
            )
    except Exception as exc:
        return {
            **_empty_rlm_pack(token_budget),
            "status": "unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }

    pack = _merge_rlm_packs(packs, token_budget=token_budget)
    pack["status"] = "ok"
    return pack


def build_role_context_pack(
    *,
    role: str,
    process_id: str,
    task: str,
    acceptance: str,
    route: dict[str, Any],
    skill_context: dict[str, Any],
    phase: str = "initial",
    events: list[dict[str, Any]] | None = None,
    assignments: list[dict[str, Any]] | None = None,
    supervisor_state: dict[str, Any] | None = None,
    previous_answer: str = "",
    prior_verdict: dict[str, Any] | None = None,
    rlm_store_path: str | Path | None = None,
    rlm_enabled: bool = False,
    workspace_root: str | None = None,
    token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
) -> dict[str, Any]:
    events = list(events or [])
    assignments = list(assignments or [])
    required_fixes = _verdict_string_list(
        prior_verdict=prior_verdict,
        events=events,
        assignments=assignments,
        key="required_fixes",
    )
    known_risks = _verdict_string_list(
        prior_verdict=prior_verdict,
        events=events,
        assignments=assignments,
        key="risks",
    )
    rlm_context = _rlm_context_pack(
        process_id=process_id,
        route=route,
        skill_context=skill_context,
        token_budget=token_budget,
        rlm_store_path=rlm_store_path,
        rlm_enabled=rlm_enabled,
    )
    pack = {
        "version": 1,
        "role": role,
        "phase": phase,
        "session_strategy": SESSION_STRATEGY,
        "process_id": process_id,
        "task": truncate_text(task),
        "acceptance": truncate_text(acceptance),
        "route": _route_summary(route),
        "role_skills": _role_skill_summary(skill_context, role),
        "workspace": _workspace_snapshot(process_id=process_id, role=role, workspace_root=workspace_root),
        "previous_attempts": _previous_attempts(assignments),
        "previous_answer_preview": truncate_text(previous_answer) if previous_answer else "",
        "required_fixes": required_fixes,
        "known_risks": known_risks,
        "human_decision": _human_decision(events, supervisor_state),
        "rlm_context": rlm_context,
        "safety": {
            "raw_secret_values_forbidden": True,
            "cookie_values_forbidden": True,
            "secrets_as_vault_refs_only": True,
            "shared_state_writer": "supervisor",
            "sqlite_single_writer": True,
            "agent_direct_state_writes_allowed": False,
        },
    }
    if role == "bot2":
        pack["review_contract"] = {
            "current_bot1_result_location": "Bot#1 result is supplied in the Bot#2 prompt outside this startup pack.",
            "approval_rule": "Approve only when acceptance criteria, evidence, required fixes, and safety gates are satisfied.",
            "reject_on": ["missing evidence", "unclosed required_fix", "unsafe external write", "test theater"],
        }
    return redact_payload(pack)


def build_role_context_packs(
    *,
    roles: list[str],
    process_id: str,
    task: str,
    acceptance: str,
    route: dict[str, Any],
    skill_context: dict[str, Any],
    phase: str = "initial",
    events: list[dict[str, Any]] | None = None,
    assignments: list[dict[str, Any]] | None = None,
    supervisor_state: dict[str, Any] | None = None,
    previous_answer: str = "",
    prior_verdict: dict[str, Any] | None = None,
    rlm_store_path: str | Path | None = None,
    rlm_enabled: bool = False,
    workspace_root: str | None = None,
    token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
) -> dict[str, dict[str, Any]]:
    packs: dict[str, dict[str, Any]] = {}
    for role in roles:
        packs[role] = build_role_context_pack(
            role=role,
            process_id=process_id,
            task=task,
            acceptance=acceptance,
            route=route,
            skill_context=skill_context,
            phase=phase,
            events=events,
            assignments=assignments,
            supervisor_state=supervisor_state,
            previous_answer=previous_answer,
            prior_verdict=prior_verdict,
            rlm_store_path=rlm_store_path,
            rlm_enabled=rlm_enabled,
            workspace_root=workspace_root,
            token_budget=token_budget,
        )
    return packs


def event_payload(role_context_packs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    roles: dict[str, Any] = {}
    for role, pack in sorted(role_context_packs.items()):
        rlm_context = pack.get("rlm_context") or {}
        workspace = pack.get("workspace") or {}
        roles[role] = {
            "phase": pack.get("phase", ""),
            "estimated_tokens": rlm_context.get("estimated_tokens", 0),
            "token_budget": rlm_context.get("token_budget", 0),
            "record_ids": [record.get("id") for record in (rlm_context.get("records") or [])],
            "required_fix_count": len(pack.get("required_fixes") or []),
            "workspace": {
                "path": workspace.get("path", ""),
                "exists": bool(workspace.get("exists")),
                "status": workspace.get("status", ""),
            },
        }
    return redact_payload({"session_strategy": SESSION_STRATEGY, "roles": roles})


def assignment_payload(role_context_packs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return redact_payload(
        {
            "session_strategy": SESSION_STRATEGY,
            "summary": event_payload(role_context_packs),
            "packs": role_context_packs,
        }
    )
