#!/usr/bin/env python3
"""Process-oriented Hermes Supervisor MVP."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from human_notification import (
    build_human_notification_payload,
    dispatch_human_notification,
    redact_payload,
)
from skill_index import load_manifest as load_skill_manifest
from skill_index import select_skill_context
from supervisor_common import (
    BOT2_VERDICT_STATUSES,
    INVALID_BOT2_STATUS,
    MAX_BOT_REVIEW_CYCLES,
    NO_MEANING,
    YES_MEANING,
    add_event as add_supervisor_event,
    add_role_run,
    create_human_escalation,
    create_task,
    dumps,
    escalation_text,
    extract_bot2_verdict,
    get_task,
    link_bot2,
    parse_bot2_verdict,
    record_human_decision,
    supervisor_status_for_verdict,
    task_details,
    update_task,
)
from task_router import apply_classification_audit, classify_task as classify_task_uncached, parse_classification_audit


PROCESS_STORE_PATH = Path(
    os.environ.get(
        "PROCESS_STORE_PATH",
        "/var/lib/docker/volumes/hermes-data/_data/process_orchestrator_store.db",
    )
)
ROUTE_AUDIT_CACHE_VERSION = "route-audit-v1"
PROCESS_ROUTE_CACHE_VERSION = "process-route-v1"
_PROCESS_ROUTE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_ROUTE_AUDIT_MEMORY_CACHE: dict[str, tuple[float, str, int, dict[str, Any]]] = {}
_INITIALIZED_PROCESS_STORES: set[str] = set()


def _runtime_cache_enabled() -> bool:
    return os.environ.get("HERMES_PROCESS_RAM_CACHE", "1").strip().lower() not in {"0", "false", "no", "off"}


def _runtime_cache_ttl_seconds() -> int:
    raw = os.environ.get("HERMES_PROCESS_RAM_CACHE_TTL_SECONDS", "300")
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _runtime_cache_size(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def clear_runtime_caches() -> None:
    _PROCESS_ROUTE_CACHE.clear()
    _ROUTE_AUDIT_MEMORY_CACHE.clear()
    _INITIALIZED_PROCESS_STORES.clear()


def runtime_cache_stats() -> dict[str, int]:
    return {
        "route_entries": len(_PROCESS_ROUTE_CACHE),
        "route_audit_entries": len(_ROUTE_AUDIT_MEMORY_CACHE),
    }


def _remember_lru(cache: dict[str, Any], key: str, value: Any, *, max_size: int) -> None:
    if max_size <= 0:
        return
    while len(cache) >= max_size:
        cache.pop(next(iter(cache)))
    cache[key] = value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def capped_llm_tokens(requested: int, *, env_name: str, default_cap: int = 0) -> int:
    raw = os.environ.get(env_name, "").strip()
    if raw.lower() in {"off", "false", "no", "none"}:
        return max(1, requested)
    if raw:
        try:
            cap = int(raw)
        except ValueError:
            cap = default_cap
    else:
        cap = default_cap
    if cap <= 0:
        return max(1, requested)
    return max(1, min(requested, cap))


TOKEN_POLICY_PROFILES: dict[str, dict[str, int]] = {
    "L0": {"bot1": 384, "bot1_revision": 512, "bot1_self_check": 512, "bot2_verdict": 384, "bot2_repair": 384},
    "L1": {"bot1": 512, "bot1_revision": 700, "bot1_self_check": 700, "bot2_verdict": 512, "bot2_repair": 384},
    "L2": {"bot1": 900, "bot1_revision": 1100, "bot1_self_check": 900, "bot2_verdict": 1000, "bot2_repair": 900},
    "L3": {"bot1": 1400, "bot1_revision": 1400, "bot1_self_check": 1200, "bot2_verdict": 1200, "bot2_repair": 1000},
    "L4": {"bot1": 0, "bot1_revision": 0, "bot1_self_check": 0, "bot2_verdict": 1000, "bot2_repair": 800},
}
ROLE_ENV_TOKEN_CAPS = {
    "bot1": "HERMES_BOT1_MAX_TOKENS",
    "bot1_revision": "HERMES_BOT1_MAX_TOKENS",
    "bot1_self_check": "HERMES_BOT1_MAX_TOKENS",
    "bot2_verdict": "HERMES_BOT2_VERDICT_MAX_TOKENS",
    "bot2_repair": "HERMES_BOT2_REPAIR_MAX_TOKENS",
}
REVIEW_CYCLE_POLICY_PROFILES = {
    "L0": 1,
    "L1": 1,
    "L2": 2,
    "L3": 2,
    "L4": 2,
}
EXTENDED_L3_REVIEW_SIGNALS = {
    "migration",
    "database",
    "postgres",
    "sqlite",
    "deploy",
    "rollback",
    "code",
    "implementation",
    "refactor",
}


def adaptive_token_budget_enabled() -> bool:
    return os.environ.get("HERMES_ADAPTIVE_TOKEN_BUDGET", "1").strip().lower() not in {"0", "false", "no", "off"}


def token_policy_level(route: dict[str, Any] | None = None) -> str:
    route = route or {}
    level = str(route.get("task_level") or "L3").upper()
    if level not in TOKEN_POLICY_PROFILES:
        level = "L3"
    if bool(route.get("human_gate_required")):
        return "L4"
    return level


def token_budget_for_role(
    requested: int,
    *,
    role: str,
    route: dict[str, Any] | None = None,
) -> int:
    requested = max(1, int(requested))
    env_name = ROLE_ENV_TOKEN_CAPS.get(role, "")
    if env_name and os.environ.get(env_name, "").strip():
        return capped_llm_tokens(requested, env_name=env_name)
    if not adaptive_token_budget_enabled():
        return requested
    level = token_policy_level(route)
    cap = TOKEN_POLICY_PROFILES[level].get(role, 0)
    return requested if cap <= 0 else max(1, min(requested, cap))


def token_policy_snapshot(
    *,
    requested: int,
    route: dict[str, Any] | None,
    budgets: dict[str, int],
) -> dict[str, Any]:
    route = route or {}
    return {
        "enabled": adaptive_token_budget_enabled(),
        "level": token_policy_level(route),
        "route_level": str(route.get("task_level") or ""),
        "risk_level": str(route.get("risk_level") or ""),
        "human_gate_required": bool(route.get("human_gate_required")),
        "requested_max_tokens": max(1, int(requested)),
        "budgets": dict(budgets),
    }


def adaptive_review_cycles_enabled() -> bool:
    return os.environ.get("HERMES_ADAPTIVE_REVIEW_CYCLES", "1").strip().lower() not in {"0", "false", "no", "off"}


def review_cycle_policy_for_route(task: str = "", route: dict[str, Any] | None = None) -> dict[str, Any]:
    route = route or {}
    level = token_policy_level(route)
    if not adaptive_review_cycles_enabled():
        return {
            "enabled": False,
            "level": level,
            "source": "global_max",
            "global_max_cycles": MAX_BOT_REVIEW_CYCLES,
            "effective_max_cycles": max(1, MAX_BOT_REVIEW_CYCLES),
            "extended_l3": False,
        }

    policy_max = REVIEW_CYCLE_POLICY_PROFILES.get(level, REVIEW_CYCLE_POLICY_PROFILES["L3"])
    task_type = str(route.get("task_type") or "")
    process_plan = " ".join(str(item) for item in (route.get("process_plan") or []))
    signal_text = f"{task} {task_type} {process_plan}".lower()
    extended_l3 = level == "L3" and any(signal in signal_text for signal in EXTENDED_L3_REVIEW_SIGNALS)
    if extended_l3:
        policy_max = 3
    effective_max = max(1, min(MAX_BOT_REVIEW_CYCLES, policy_max))
    return {
        "enabled": True,
        "level": level,
        "source": "adaptive",
        "global_max_cycles": MAX_BOT_REVIEW_CYCLES,
        "policy_max_cycles": policy_max,
        "effective_max_cycles": effective_max,
        "extended_l3": extended_l3,
    }


def llm_http_timing(response: dict[str, Any]) -> dict[str, Any]:
    timing = response.get("_hermes_http_timing_ms") if isinstance(response, dict) else {}
    return dict(timing) if isinstance(timing, dict) else {}


def llm_completion_budget(response: dict[str, Any], *, max_tokens: int) -> dict[str, Any]:
    usage = response.get("usage") if isinstance(response, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    meta = response.get("_hermes_response_meta") if isinstance(response, dict) else {}
    meta = meta if isinstance(meta, dict) else {}
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(completion_tokens, int):
        completion_tokens = 0
    finish_reason = str(meta.get("finish_reason") or "")
    over_budget = max_tokens > 0 and completion_tokens > max_tokens + 8
    return {
        "max_tokens": max_tokens,
        "completion_tokens": completion_tokens,
        "finish_reason": finish_reason,
        "content_chars": int(meta.get("content_chars") or 0),
        "hit_cap": finish_reason == "length"
        or (max_tokens > 0 and 0 < completion_tokens <= max_tokens and completion_tokens >= max_tokens - 8),
        "over_budget": over_budget,
    }


def process_id() -> str:
    return f"proc-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def classify_task(task: str) -> dict[str, Any]:
    if not _runtime_cache_enabled():
        return classify_task_uncached(task)
    normalized = " ".join(task.strip().split()).lower()
    raw = json.dumps({"task": normalized, "version": PROCESS_ROUTE_CACHE_VERSION}, ensure_ascii=False, sort_keys=True)
    cache_key = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    cached = _PROCESS_ROUTE_CACHE.get(cache_key)
    now = time.monotonic()
    if cached:
        expires_at, route = cached
        if now <= expires_at:
            return copy.deepcopy(route)
        _PROCESS_ROUTE_CACHE.pop(cache_key, None)
    route = classify_task_uncached(task)
    _remember_lru(
        _PROCESS_ROUTE_CACHE,
        cache_key,
        (now + _runtime_cache_ttl_seconds(), copy.deepcopy(route)),
        max_size=_runtime_cache_size("HERMES_PROCESS_ROUTE_CACHE_SIZE", 512),
    )
    return copy.deepcopy(route)


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    store = Path(path or PROCESS_STORE_PATH)
    store.parent.mkdir(parents=True, exist_ok=True)
    store_key = str(store)
    existed_before = store.exists()
    con = sqlite3.connect(store)
    con.row_factory = sqlite3.Row
    if not existed_before or store_key not in _INITIALIZED_PROCESS_STORES:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS process_runs (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                task TEXT NOT NULL,
                acceptance TEXT NOT NULL,
                router_json TEXT NOT NULL,
                supervisor_task_id TEXT NOT NULL,
                status TEXT NOT NULL,
                current_phase TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS process_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                process_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY(process_id) REFERENCES process_runs(id)
            );

            CREATE TABLE IF NOT EXISTS process_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                process_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                worker TEXT NOT NULL,
                phase TEXT NOT NULL,
                status TEXT NOT NULL,
                output_json TEXT NOT NULL,
                FOREIGN KEY(process_id) REFERENCES process_runs(id)
            );

            CREATE TABLE IF NOT EXISTS route_audit_cache (
                cache_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                model TEXT NOT NULL,
                route_level TEXT NOT NULL,
                route_risk TEXT NOT NULL,
                audit_json TEXT NOT NULL,
                hits INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        con.commit()
        _INITIALIZED_PROCESS_STORES.add(store_key)
    return con


def add_process_event(pid: str, event_type: str, payload: dict[str, Any], *, store_path: Path | str | None = None) -> None:
    payload = redact_payload(payload)
    with connect(store_path) as con:
        con.execute(
            "INSERT INTO process_events(process_id, created_at, event_type, payload_json) VALUES (?, ?, ?, ?)",
            (pid, utc_now(), event_type, dumps(payload)),
        )
        con.commit()


def add_assignment(
    pid: str,
    worker: str,
    phase: str,
    status: str,
    output: dict[str, Any],
    *,
    store_path: Path | str | None = None,
) -> None:
    with connect(store_path) as con:
        con.execute(
            """
            INSERT INTO process_assignments(process_id, created_at, worker, phase, status, output_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (pid, utc_now(), worker, phase, status, dumps(output)),
        )
        con.commit()


def update_process(
    pid: str,
    *,
    status: str,
    current_phase: str,
    store_path: Path | str | None = None,
) -> None:
    with connect(store_path) as con:
        con.execute(
            "UPDATE process_runs SET updated_at=?, status=?, current_phase=? WHERE id=?",
            (utc_now(), status, current_phase, pid),
        )
        con.commit()


def create_process_run(
    *,
    task: str,
    acceptance: str,
    route: dict[str, Any],
    supervisor_task_id: str,
    store_path: Path | str | None = None,
) -> str:
    pid = process_id()
    now = utc_now()
    with connect(store_path) as con:
        con.execute(
            """
            INSERT INTO process_runs
              (id, created_at, updated_at, task, acceptance, router_json, supervisor_task_id, status, current_phase)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (pid, now, now, task, acceptance, dumps(route), supervisor_task_id, "created", "router"),
        )
        con.commit()
    return pid


def parse_verdict(text: str) -> dict[str, Any]:
    return extract_bot2_verdict(text)


def route_requires_bot1(route: dict[str, Any]) -> bool:
    return route.get("task_level") != "L0" and "bot1" in route.get("process_plan", [])


def route_requires_tester(route: dict[str, Any]) -> bool:
    return "tester" in route.get("process_plan", []) and bool(
        route.get("review_required") or route.get("task_level") in {"L3", "L4"}
    )


def route_requires_bot2(route: dict[str, Any]) -> bool:
    return bool(route.get("review_required") or route.get("human_gate_required") or route.get("task_level") in {"L3", "L4"})


def build_route_skill_context(route: dict[str, Any], *, include_approval_required: bool = False) -> dict[str, Any]:
    manifest = load_skill_manifest()
    return select_skill_context(
        manifest,
        route=route,
        include_approval_required=include_approval_required,
    )


def skill_context_for_role(skill_context: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "role": role,
        "skills": (skill_context.get("roles") or {}).get(role, []),
        "gated_skills": (skill_context.get("gated_roles") or {}).get(role, []),
        "task_tags": skill_context.get("task_tags", []),
        "runtime_contract": skill_context.get("runtime_contract", {}),
    }


def dry_bot1_result(task: str, acceptance: str, route: dict[str, Any]) -> str:
    return (
        "Bot#1 dry-run result\n"
        f"- task_level: {route['task_level']}\n"
        f"- task_type: {route['task_type']}\n"
        "- changed_files: none\n"
        "- tests: dry-run evidence only\n"
        f"- task: {task}\n"
        f"- acceptance: {acceptance}\n"
    )


def dry_bot1_revision_result(
    task: str,
    acceptance: str,
    previous_answer: str,
    verdict: dict[str, Any],
    route: dict[str, Any],
) -> str:
    fixes = verdict.get("required_fixes") or []
    risks = verdict.get("risks") or []
    return (
        "Bot#1 dry-run revised result\n"
        f"- task_level: {route['task_level']}\n"
        f"- task_type: {route['task_type']}\n"
        "- source: human_agreed_with_bot2\n"
        f"- previous_answer_chars: {len(previous_answer)}\n"
        f"- applied_required_fixes: {json.dumps(fixes, ensure_ascii=False)}\n"
        f"- acknowledged_risks: {json.dumps(risks, ensure_ascii=False)}\n"
        "- tests: dry-run revision evidence only\n"
        f"- task: {task}\n"
        f"- acceptance: {acceptance}\n"
    )


def pre_human_gate_result(task: str, acceptance: str, route: dict[str, Any], *, live_dual_requested: bool) -> str:
    return (
        "Pre-human-gate policy result\n"
        "- status: awaiting explicit human decision before any LLM execution or external write\n"
        f"- task_level: {route['task_level']}\n"
        f"- task_type: {route['task_type']}\n"
        f"- risk_level: {route['risk_level']}\n"
        f"- live_dual_deferred_until_yes: {str(bool(live_dual_requested)).lower()}\n"
        "- reason: route requires human_gate_required=true before Bot#1/Bot#2/DevOps can continue\n"
        "- next_step: ask the user Да/Нет; on Да, run Bot#1, Tester, and Bot#2 before any external write\n"
        f"- task: {task}\n"
        f"- acceptance: {acceptance}\n"
    )


def dry_verdict(status: str) -> dict[str, Any]:
    normalized = status.upper()
    approved = normalized in {"APPROVE", "APPROVE_WITH_EVIDENCE"}
    return {
        "status": normalized,
        "summary": f"Dry Bot#2 verdict: {normalized}",
        "approved_action": "execute" if approved else "needs_human",
        "evidence_checked": ["dry-run evidence package"],
        "risks": [] if approved else ["dry_run_risk_for_human_review"],
        "required_fixes": [] if approved else ["Resolve Bot#1/Bot#2 disagreement or ask user Da/Net."],
        "confidence": 0.9 if approved else 0.0 if normalized == "INVALID_BOT2_OUTPUT" else 0.65,
    }


def configured_bot2_verdict(args: argparse.Namespace) -> dict[str, Any]:
    if not args.bot2_verdict_json:
        return dry_verdict(args.bot2_status)
    verdict = parse_verdict(args.bot2_verdict_json)
    if verdict.get("status") == INVALID_BOT2_STATUS:
        raise SystemExit("--bot2-verdict-json must be a valid Bot#2 verdict JSON object")
    return verdict


def safe_route_audit_fast_path(route: dict[str, Any]) -> bool:
    return (
        route.get("task_level") in {"L0", "L1"}
        and route.get("risk_level") == "low"
        and not bool(route.get("review_required"))
        and not bool(route.get("human_gate_required"))
    )


def skipped_route_audit(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "SKIPPED_LOW_RISK_FAST_PATH",
        "source": "supervisor_route_audit_policy",
        "recommended_level": route.get("task_level", ""),
        "risk_level": route.get("risk_level", ""),
        "review_required": bool(route.get("review_required")),
        "human_gate_required": bool(route.get("human_gate_required")),
        "summary": "Live Bot#2 classification audit skipped for deterministic low-risk L0/L1 route.",
        "signals": ["task_level_low", "risk_low", "no_review_gate", "no_human_gate"],
        "audit_skipped": True,
    }


def route_audit_mode(args: argparse.Namespace) -> str:
    mode = str(getattr(args, "route_audit_mode", "auto") or "auto").lower()
    return mode if mode in {"auto", "always"} else "auto"


def route_audit_cache_key(task: str, route: dict[str, Any], model: str) -> str:
    route_fingerprint = {
        "task": " ".join(task.strip().split()).lower(),
        "model": model,
        "task_level": route.get("task_level", ""),
        "task_type": route.get("task_type", ""),
        "risk_level": route.get("risk_level", ""),
        "review_required": bool(route.get("review_required")),
        "human_gate_required": bool(route.get("human_gate_required")),
        "process_plan": route.get("process_plan", []),
        "version": ROUTE_AUDIT_CACHE_VERSION,
    }
    raw = json.dumps(route_fingerprint, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _route_audit_memory_key(cache_key: str, store_path: Path | str | None = None) -> str:
    store = str(Path(store_path or PROCESS_STORE_PATH))
    return f"{store}:{cache_key}"


def _cache_audit_payload(audit: dict[str, Any], *, cached_at: str, hits: int) -> dict[str, Any]:
    payload = copy.deepcopy(audit)
    original_latency_ms = int(payload.get("latency_ms") or 0)
    payload["source"] = "bot2_live_route_audit_cache"
    payload["cache_hit"] = True
    payload["cached_at"] = cached_at
    payload["cache_hits"] = hits
    payload["original_latency_ms"] = original_latency_ms
    payload["latency_ms"] = 0
    return payload


def _remember_route_audit_memory(
    *,
    cache_key: str,
    store_path: Path | str | None,
    created_at: str,
    hits: int,
    audit: dict[str, Any],
) -> None:
    if not _runtime_cache_enabled():
        return
    memory_key = _route_audit_memory_key(cache_key, store_path)
    _remember_lru(
        _ROUTE_AUDIT_MEMORY_CACHE,
        memory_key,
        (time.monotonic() + _runtime_cache_ttl_seconds(), created_at, hits, copy.deepcopy(audit)),
        max_size=_runtime_cache_size("HERMES_ROUTE_AUDIT_MEMORY_CACHE_SIZE", 256),
    )


def _cached_route_audit_memory(
    *,
    cache_key: str,
    store_path: Path | str | None,
) -> dict[str, Any]:
    if not _runtime_cache_enabled():
        return {}
    memory_key = _route_audit_memory_key(cache_key, store_path)
    cached = _ROUTE_AUDIT_MEMORY_CACHE.get(memory_key)
    if not cached:
        return {}
    expires_at, created_at, hits, audit = cached
    if time.monotonic() > expires_at:
        _ROUTE_AUDIT_MEMORY_CACHE.pop(memory_key, None)
        return {}
    hits += 1
    _ROUTE_AUDIT_MEMORY_CACHE[memory_key] = (expires_at, created_at, hits, audit)
    return _cache_audit_payload(audit, cached_at=created_at, hits=hits)


def cached_route_audit(
    *,
    cache_key: str,
    store_path: Path | str | None = None,
) -> dict[str, Any]:
    memory_hit = _cached_route_audit_memory(cache_key=cache_key, store_path=store_path)
    if memory_hit:
        return memory_hit

    with connect(store_path) as con:
        row = con.execute(
            "SELECT created_at, audit_json, hits FROM route_audit_cache WHERE cache_key=?",
            (cache_key,),
        ).fetchone()
        if not row:
            return {}
        hits = int(row["hits"] or 0) + 1
        con.execute(
            "UPDATE route_audit_cache SET updated_at=?, hits=? WHERE cache_key=?",
            (utc_now(), hits, cache_key),
        )
        con.commit()
    try:
        audit = json.loads(row["audit_json"])
    except json.JSONDecodeError:
        return {}
    _remember_route_audit_memory(
        cache_key=cache_key,
        store_path=store_path,
        created_at=str(row["created_at"]),
        hits=hits,
        audit=audit,
    )
    return _cache_audit_payload(audit, cached_at=str(row["created_at"]), hits=hits)


def store_route_audit_cache(
    *,
    cache_key: str,
    model: str,
    route: dict[str, Any],
    audit: dict[str, Any],
    store_path: Path | str | None = None,
) -> None:
    if audit.get("audit_skipped") or audit.get("status") == "INVALID_CLASSIFICATION_AUDIT":
        return
    now = utc_now()
    with connect(store_path) as con:
        con.execute(
            """
            INSERT INTO route_audit_cache
              (cache_key, created_at, updated_at, model, route_level, route_risk, audit_json, hits)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(cache_key) DO UPDATE SET
              updated_at=excluded.updated_at,
              model=excluded.model,
              route_level=excluded.route_level,
              route_risk=excluded.route_risk,
              audit_json=excluded.audit_json
            """,
            (
                cache_key,
                now,
                now,
                model,
                str(route.get("task_level") or ""),
                str(route.get("risk_level") or ""),
                dumps(audit),
            ),
        )
        con.commit()
    _remember_route_audit_memory(
        cache_key=cache_key,
        store_path=store_path,
        created_at=now,
        hits=0,
        audit=audit,
    )


def route_audit_from_args(args: argparse.Namespace, task: str, route: dict[str, Any]) -> dict[str, Any]:
    if getattr(args, "bot2_route_audit_json", ""):
        return parse_classification_audit(args.bot2_route_audit_json)
    if not getattr(args, "live_route_audit", False):
        return {}
    mode = route_audit_mode(args)
    if mode == "auto" and safe_route_audit_fast_path(route):
        return skipped_route_audit(route)

    cache_key = route_audit_cache_key(task, route, args.bot2_model)
    use_cache = mode == "auto" and not bool(getattr(args, "no_route_audit_cache", False))
    if use_cache:
        audit = cached_route_audit(cache_key=cache_key, store_path=args.process_store)
        if audit:
            return audit

    import dual_bot_lab as lab

    cfg = lab.bothub_config()
    started_at = time.perf_counter()
    audit_raw, audit_response = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=args.bot2_model,
        messages=lab.bot2_route_audit_messages(task, route),
        max_tokens=min(args.max_tokens, 700),
        timeout=args.timeout,
    )
    audit = parse_classification_audit(audit_raw)
    audit["source"] = "bot2_live_route_audit"
    audit["raw_chars"] = len(audit_raw)
    audit["usage"] = audit_response.get("usage", {})
    audit["http_timing_ms"] = llm_http_timing(audit_response)
    audit["latency_ms"] = elapsed_ms(started_at)
    audit["model"] = args.bot2_model
    if use_cache:
        store_route_audit_cache(
            cache_key=cache_key,
            model=args.bot2_model,
            route=route,
            audit=audit,
            store_path=args.process_store,
        )
    return audit


def live_bot1_result(
    task: str,
    acceptance: str,
    *,
    bot1_model: str,
    max_tokens: int,
    timeout: int,
    skill_context: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> tuple[str, str, str]:
    import dual_bot_lab as lab

    cfg = lab.bothub_config()
    bot1_max_tokens = token_budget_for_role(max_tokens, role="bot1", route=route)
    semantic_budget = lab.semantic_budget_for_route(route, "bot1")
    rid = lab.run_id()
    lab.add_run(rid, task, acceptance, bot1_model, "")
    started_at = time.perf_counter()
    bot1, bot1_raw = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=bot1_model,
        messages=lab.bot1_messages(
            task,
            acceptance,
            skill_context=skill_context or {},
            semantic_budget=semantic_budget,
        ),
        max_tokens=bot1_max_tokens,
        timeout=timeout,
    )
    lab.add_message(
        rid,
        "Bot#1",
        bot1_model,
        bot1,
        {
            "usage": bot1_raw.get("usage", {}),
            "latency_ms": elapsed_ms(started_at),
            "http_timing_ms": llm_http_timing(bot1_raw),
            "completion_budget": llm_completion_budget(bot1_raw, max_tokens=bot1_max_tokens),
            "semantic_budget": semantic_budget,
            "token_policy": token_policy_snapshot(
                requested=max_tokens,
                route=route,
                budgets={"bot1": bot1_max_tokens},
            ),
        },
    )
    report = lab.write_report(
        run_id_value=rid,
        task=task,
        acceptance=acceptance,
        bot1_model=bot1_model,
        bot1_result=bot1,
        bot2_model="not-required",
        bot2_result="Bot#2 was not required by route policy.",
    )
    lab.update_run(rid, "completed", str(report))
    return bot1, rid, str(report)


def live_dual_result(
    task: str,
    acceptance: str,
    *,
    bot1_model: str,
    bot2_model: str,
    max_tokens: int,
    timeout: int,
    skill_context: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> tuple[str, str, dict[str, Any], str]:
    import dual_bot_lab as lab

    cfg = lab.bothub_config()
    token_budgets = {
        "bot1": token_budget_for_role(max_tokens, role="bot1", route=route),
        "bot1_revision": token_budget_for_role(max_tokens, role="bot1_revision", route=route),
        "bot1_self_check": token_budget_for_role(max_tokens, role="bot1_self_check", route=route),
        "bot2_verdict": token_budget_for_role(max_tokens, role="bot2_verdict", route=route),
        "bot2_repair": token_budget_for_role(max_tokens, role="bot2_repair", route=route),
    }
    token_policy = token_policy_snapshot(requested=max_tokens, route=route, budgets=token_budgets)
    semantic_budgets = {
        "bot1": lab.semantic_budget_for_route(route, "bot1"),
        "bot1_revision": lab.semantic_budget_for_route(route, "bot1_revision"),
        "bot1_self_check": lab.semantic_budget_for_route(route, "bot1_self_check"),
        "bot2": lab.semantic_budget_for_route(route, "bot2"),
        "bot2_repair": lab.semantic_budget_for_route(route, "bot2"),
    }
    review_policy = review_cycle_policy_for_route(task, route)
    max_review_cycles = int(review_policy["effective_max_cycles"])
    rid = lab.run_id()
    lab.add_run(rid, task, acceptance, bot1_model, bot2_model)
    review_cycles: list[dict[str, Any]] = []
    bot1 = ""
    bot2 = ""
    verdict: dict[str, Any] = {}

    for round_no in range(1, max_review_cycles + 1):
        if round_no == 1:
            bot1_max_tokens = token_budgets["bot1"]
            bot1_messages = lab.bot1_messages(
                task,
                acceptance,
                skill_context=skill_context_for_role(skill_context or {}, "bot1"),
                semantic_budget=semantic_budgets["bot1"],
            )
            bot1_speaker = "Bot#1"
        else:
            bot1_max_tokens = token_budgets["bot1_revision"]
            bot1_messages = lab.bot1_revision_messages(
                task,
                acceptance,
                bot1,
                verdict,
                round_no - 1,
                skill_context=skill_context_for_role(skill_context or {}, "bot1"),
                semantic_budget=semantic_budgets["bot1_revision"],
            )
            bot1_speaker = f"Bot#1-revision-{round_no}"
        bot1_started_at = time.perf_counter()
        bot1, bot1_raw = lab.call_chat(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=bot1_model,
            messages=bot1_messages,
            max_tokens=bot1_max_tokens,
            timeout=timeout,
        )
        bot1_latency_ms = elapsed_ms(bot1_started_at)
        lab.add_message(
            rid,
            bot1_speaker,
            bot1_model,
            bot1,
            {
                "usage": bot1_raw.get("usage", {}),
                "latency_ms": bot1_latency_ms,
                "http_timing_ms": llm_http_timing(bot1_raw),
            },
        )

        self_check = ""
        fix_closure_checklist: list[dict[str, str]] = []
        self_check_latency_ms = 0
        self_check_usage: dict[str, Any] = {}
        self_check_http_timing: dict[str, Any] = {}
        if round_no > 1:
            self_check_started_at = time.perf_counter()
            self_check, self_check_raw = lab.call_chat(
                base_url=cfg["base_url"],
                api_key=cfg["api_key"],
                model=bot1_model,
                messages=lab.bot1_self_check_messages(
                    task,
                    acceptance,
                    bot1,
                    verdict,
                    round_no,
                    skill_context=skill_context_for_role(skill_context or {}, "bot1"),
                    semantic_budget=semantic_budgets["bot1_self_check"],
                ),
                max_tokens=token_budgets["bot1_self_check"],
                timeout=timeout,
            )
            self_check_latency_ms = elapsed_ms(self_check_started_at)
            self_check_usage = self_check_raw.get("usage", {})
            self_check_http_timing = llm_http_timing(self_check_raw)
            lab.add_message(
                rid,
                f"Bot#1-self-check-{round_no}",
                bot1_model,
                self_check,
                {
                    "usage": self_check_usage,
                    "latency_ms": self_check_latency_ms,
                    "http_timing_ms": self_check_http_timing,
                },
            )
            bot1 = self_check
            fix_closure_checklist = [
                {
                    "required_fix": str(fix),
                    "status": "claimed_closed_by_bot1_self_check",
                    "evidence": f"Bot#1 self-check round {round_no}",
                }
                for fix in (verdict.get("required_fixes") or [])
            ]

        bot2_started_at = time.perf_counter()
        bot2, bot2_raw = lab.call_chat(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=bot2_model,
            messages=lab.bot2_messages(
                task,
                acceptance,
                bot1,
                skill_context=skill_context_for_role(skill_context or {}, "bot2"),
                semantic_budget=semantic_budgets["bot2"],
            ),
            max_tokens=token_budgets["bot2_verdict"],
            timeout=timeout,
        )
        bot2_latency_ms = elapsed_ms(bot2_started_at)
        bot2_repair_usage: dict[str, Any] = {}
        bot2_repair_http_timing: dict[str, Any] = {}
        lab.add_message(
            rid,
            f"Bot#2-{round_no}",
            bot2_model,
            bot2,
            {
                "usage": bot2_raw.get("usage", {}),
                "latency_ms": bot2_latency_ms,
                "http_timing_ms": llm_http_timing(bot2_raw),
            },
        )
        verdict = parse_verdict(bot2)
        bot2_repair_latency_ms = 0
        if verdict.get("status") == INVALID_BOT2_STATUS:
            bot2_repair_started_at = time.perf_counter()
            bot2_repair, bot2_repair_raw = lab.call_chat(
                base_url=cfg["base_url"],
                api_key=cfg["api_key"],
                model=bot2_model,
                messages=lab.bot2_repair_messages(
                    task,
                    acceptance,
                    bot1,
                    bot2,
                    semantic_budget=semantic_budgets["bot2_repair"],
                ),
                max_tokens=token_budgets["bot2_repair"],
                timeout=timeout,
            )
            bot2_repair_latency_ms = elapsed_ms(bot2_repair_started_at)
            bot2_repair_usage = bot2_repair_raw.get("usage", {})
            bot2_repair_http_timing = llm_http_timing(bot2_repair_raw)
            lab.add_message(
                rid,
                f"Bot#2-repair-{round_no}",
                bot2_model,
                bot2_repair,
                {
                    "usage": bot2_repair_usage,
                    "latency_ms": bot2_repair_latency_ms,
                    "http_timing_ms": bot2_repair_http_timing,
                },
            )
            repaired_verdict = parse_verdict(bot2_repair)
            repaired_verdict["repair_attempted"] = True
            if repaired_verdict.get("status") != INVALID_BOT2_STATUS:
                repaired_verdict["repair_status"] = "repaired"
                verdict = repaired_verdict
                bot2 = f"{bot2}\n\n## Bot#2 JSON Repair\n\n{bot2_repair}"
            else:
                verdict["repair_attempted"] = True
                verdict["repair_status"] = "failed_closed"

        loop_exhausted = verdict.get("status") == "REQUEST_CHANGES" and round_no == max_review_cycles
        if loop_exhausted:
            verdict["loop_status"] = "max_review_cycles_reached"
            risks = list(verdict.get("risks") or [])
            if "max_review_cycles_reached" not in risks:
                risks.append("max_review_cycles_reached")
            verdict["risks"] = risks
            required_fixes = list(verdict.get("required_fixes") or [])
            escalation_fix = "Escalate to a human decision after repeated Bot#1/Bot#2 correction cycles."
            if escalation_fix not in required_fixes:
                required_fixes.append(escalation_fix)
            verdict["required_fixes"] = required_fixes

        cycle = {
            "round": round_no,
            "bot1_chars": len(bot1),
            "bot1_self_check": bool(self_check),
            "bot2_status": verdict.get("status", ""),
            "bot2_summary": verdict.get("summary", ""),
            "required_fixes": verdict.get("required_fixes", []),
            "risks": verdict.get("risks", []),
            "latency_ms": {
                "bot1": bot1_latency_ms,
                "bot1_self_check": self_check_latency_ms,
                "bot2": bot2_latency_ms,
                "bot2_repair": bot2_repair_latency_ms,
            },
            "usage": {
                "bot1": bot1_raw.get("usage", {}),
                "bot1_self_check": self_check_usage,
                "bot2": bot2_raw.get("usage", {}),
                "bot2_repair": bot2_repair_usage,
            },
            "http_timing_ms": {
                "bot1": llm_http_timing(bot1_raw),
                "bot1_self_check": self_check_http_timing,
                "bot2": llm_http_timing(bot2_raw),
                "bot2_repair": bot2_repair_http_timing,
            },
            "completion_budget": {
                "bot1": llm_completion_budget(bot1_raw, max_tokens=bot1_max_tokens),
                "bot1_self_check": (
                    llm_completion_budget(self_check_raw, max_tokens=token_budgets["bot1_self_check"])
                    if self_check
                    else {}
                ),
                "bot2": llm_completion_budget(bot2_raw, max_tokens=token_budgets["bot2_verdict"]),
                "bot2_repair": (
                    llm_completion_budget(bot2_repair_raw, max_tokens=token_budgets["bot2_repair"])
                    if bot2_repair_latency_ms
                    else {}
                ),
            },
            "token_policy": token_policy,
            "review_policy": review_policy,
            "semantic_budget": semantic_budgets,
            "loop_status": verdict.get("loop_status", ""),
            "repair_loop_exhausted": loop_exhausted,
            "fix_closure_checklist": fix_closure_checklist,
            "bot2_repair_attempted": bool(verdict.get("repair_attempted")),
            "bot2_repair_status": verdict.get("repair_status", ""),
        }
        review_cycles.append(cycle)
        if verdict.get("status") in {"APPROVE", "APPROVE_WITH_EVIDENCE"}:
            break
        if verdict.get("status") != "REQUEST_CHANGES":
            break
        if loop_exhausted:
            break

    verdict["review_cycles"] = review_cycles
    verdict["token_policy"] = token_policy
    verdict["review_policy"] = review_policy
    verdict["semantic_budget"] = semantic_budgets
    final_fix_closure = next((cycle.get("fix_closure_checklist") for cycle in reversed(review_cycles) if cycle.get("fix_closure_checklist")), [])
    verdict["fix_closure_checklist"] = final_fix_closure
    report = lab.write_report(
        run_id_value=rid,
        task=task,
        acceptance=acceptance,
        bot1_model=bot1_model,
        bot1_result=bot1,
        bot2_model=bot2_model,
        bot2_result=bot2,
    )
    lab.update_run(rid, "completed", str(report))
    return bot1, rid, verdict, str(report)


def live_bot1_revision_result(
    task: str,
    acceptance: str,
    *,
    previous_answer: str,
    prior_verdict: dict[str, Any],
    bot1_model: str,
    bot2_model: str,
    max_tokens: int,
    timeout: int,
    skill_context: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> tuple[str, str, dict[str, Any], str]:
    import dual_bot_lab as lab

    cfg = lab.bothub_config()
    token_budgets = {
        "bot1_revision": token_budget_for_role(max_tokens, role="bot1_revision", route=route),
        "bot1_self_check": token_budget_for_role(max_tokens, role="bot1_self_check", route=route),
        "bot2_verdict": token_budget_for_role(max_tokens, role="bot2_verdict", route=route),
        "bot2_repair": token_budget_for_role(max_tokens, role="bot2_repair", route=route),
    }
    token_policy = token_policy_snapshot(requested=max_tokens, route=route, budgets=token_budgets)
    semantic_budgets = {
        "bot1_revision": lab.semantic_budget_for_route(route, "bot1_revision"),
        "bot1_self_check": lab.semantic_budget_for_route(route, "bot1_self_check"),
        "bot2": lab.semantic_budget_for_route(route, "bot2"),
        "bot2_repair": lab.semantic_budget_for_route(route, "bot2"),
    }
    rid = lab.run_id()
    lab.add_run(rid, task, acceptance, bot1_model, bot2_model)
    previous_cycles = list(prior_verdict.get("review_cycles") or [])
    previous_rounds = [int(cycle.get("round") or 0) for cycle in previous_cycles if isinstance(cycle, dict)]
    round_no = max(previous_rounds or [0]) + 1

    bot1_started_at = time.perf_counter()
    bot1, bot1_raw = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=bot1_model,
        messages=lab.bot1_revision_messages(
            task,
            acceptance,
            previous_answer,
            prior_verdict,
            round_no,
            skill_context=skill_context_for_role(skill_context or {}, "bot1"),
            semantic_budget=semantic_budgets["bot1_revision"],
        ),
        max_tokens=token_budgets["bot1_revision"],
        timeout=timeout,
    )
    bot1_latency_ms = elapsed_ms(bot1_started_at)
    lab.add_message(
        rid,
        f"Bot#1-human-revision-{round_no}",
        bot1_model,
        bot1,
        {
            "usage": bot1_raw.get("usage", {}),
            "latency_ms": bot1_latency_ms,
            "http_timing_ms": llm_http_timing(bot1_raw),
        },
    )

    self_check_started_at = time.perf_counter()
    self_check, self_check_raw = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=bot1_model,
        messages=lab.bot1_self_check_messages(
            task,
            acceptance,
            bot1,
            prior_verdict,
            round_no,
            skill_context=skill_context_for_role(skill_context or {}, "bot1"),
            semantic_budget=semantic_budgets["bot1_self_check"],
        ),
        max_tokens=token_budgets["bot1_self_check"],
        timeout=timeout,
    )
    self_check_latency_ms = elapsed_ms(self_check_started_at)
    lab.add_message(
        rid,
        f"Bot#1-human-self-check-{round_no}",
        bot1_model,
        self_check,
        {
            "usage": self_check_raw.get("usage", {}),
            "latency_ms": self_check_latency_ms,
            "http_timing_ms": llm_http_timing(self_check_raw),
        },
    )
    bot1 = self_check
    fix_closure_checklist = [
        {
            "required_fix": str(fix),
            "status": "claimed_closed_by_bot1_self_check",
            "evidence": f"Bot#1 human-approved self-check round {round_no}",
        }
        for fix in (prior_verdict.get("required_fixes") or [])
    ]

    bot2_started_at = time.perf_counter()
    bot2, bot2_raw = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=bot2_model,
        messages=lab.bot2_messages(
            task,
            acceptance,
            bot1,
            skill_context=skill_context_for_role(skill_context or {}, "bot2"),
            semantic_budget=semantic_budgets["bot2"],
        ),
        max_tokens=token_budgets["bot2_verdict"],
        timeout=timeout,
    )
    bot2_latency_ms = elapsed_ms(bot2_started_at)
    lab.add_message(
        rid,
        f"Bot#2-human-review-{round_no}",
        bot2_model,
        bot2,
        {
            "usage": bot2_raw.get("usage", {}),
            "latency_ms": bot2_latency_ms,
            "http_timing_ms": llm_http_timing(bot2_raw),
        },
    )

    verdict = parse_verdict(bot2)
    bot2_repair_latency_ms = 0
    bot2_repair_usage: dict[str, Any] = {}
    bot2_repair_http_timing: dict[str, Any] = {}
    if verdict.get("status") == INVALID_BOT2_STATUS:
        bot2_repair_started_at = time.perf_counter()
        bot2_repair, bot2_repair_raw = lab.call_chat(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=bot2_model,
            messages=lab.bot2_repair_messages(
                task,
                acceptance,
                bot1,
                bot2,
                semantic_budget=semantic_budgets["bot2_repair"],
            ),
            max_tokens=token_budgets["bot2_repair"],
            timeout=timeout,
        )
        bot2_repair_latency_ms = elapsed_ms(bot2_repair_started_at)
        bot2_repair_usage = bot2_repair_raw.get("usage", {})
        bot2_repair_http_timing = llm_http_timing(bot2_repair_raw)
        lab.add_message(
            rid,
            f"Bot#2-human-repair-{round_no}",
            bot2_model,
            bot2_repair,
            {
                "usage": bot2_repair_usage,
                "latency_ms": bot2_repair_latency_ms,
                "http_timing_ms": bot2_repair_http_timing,
            },
        )
        repaired_verdict = parse_verdict(bot2_repair)
        repaired_verdict["repair_attempted"] = True
        if repaired_verdict.get("status") != INVALID_BOT2_STATUS:
            repaired_verdict["repair_status"] = "repaired"
            verdict = repaired_verdict
            bot2 = f"{bot2}\n\n## Bot#2 JSON Repair\n\n{bot2_repair}"
        else:
            verdict["repair_attempted"] = True
            verdict["repair_status"] = "failed_closed"

    cycle = {
        "round": round_no,
        "human_continue": True,
        "bot1_chars": len(bot1),
        "bot1_self_check": True,
        "bot2_status": verdict.get("status", ""),
        "bot2_summary": verdict.get("summary", ""),
        "required_fixes": verdict.get("required_fixes", []),
        "risks": verdict.get("risks", []),
        "latency_ms": {
            "bot1": bot1_latency_ms,
            "bot1_self_check": self_check_latency_ms,
            "bot2": bot2_latency_ms,
            "bot2_repair": bot2_repair_latency_ms,
        },
        "usage": {
            "bot1": bot1_raw.get("usage", {}),
            "bot1_self_check": self_check_raw.get("usage", {}),
            "bot2": bot2_raw.get("usage", {}),
            "bot2_repair": bot2_repair_usage,
        },
        "http_timing_ms": {
            "bot1": llm_http_timing(bot1_raw),
            "bot1_self_check": llm_http_timing(self_check_raw),
            "bot2": llm_http_timing(bot2_raw),
            "bot2_repair": bot2_repair_http_timing,
        },
        "completion_budget": {
            "bot1": llm_completion_budget(bot1_raw, max_tokens=token_budgets["bot1_revision"]),
            "bot1_self_check": llm_completion_budget(self_check_raw, max_tokens=token_budgets["bot1_self_check"]),
            "bot2": llm_completion_budget(bot2_raw, max_tokens=token_budgets["bot2_verdict"]),
            "bot2_repair": (
                llm_completion_budget(bot2_repair_raw, max_tokens=token_budgets["bot2_repair"])
                if bot2_repair_latency_ms
                else {}
            ),
        },
        "token_policy": token_policy,
        "semantic_budget": semantic_budgets,
        "fix_closure_checklist": fix_closure_checklist,
        "bot2_repair_attempted": bool(verdict.get("repair_attempted")),
        "bot2_repair_status": verdict.get("repair_status", ""),
    }
    verdict["review_cycles"] = previous_cycles + [cycle]
    verdict["token_policy"] = token_policy
    verdict["semantic_budget"] = semantic_budgets
    verdict["fix_closure_checklist"] = fix_closure_checklist
    report = lab.write_report(
        run_id_value=rid,
        task=task,
        acceptance=acceptance,
        bot1_model=bot1_model,
        bot1_result=bot1,
        bot2_model=bot2_model,
        bot2_result=bot2,
    )
    lab.update_run(rid, "completed", str(report))
    return bot1, rid, verdict, str(report)


def route_policy_verdict(*, pre_human_gate: bool = False, live_dual_deferred: bool = False) -> dict[str, Any]:
    return {
        "status": "NEEDS_HUMAN",
        "summary": "Route policy requires explicit human approval before this action can continue.",
        "risks": ["route_human_gate_required"],
        "required_fixes": [
            (
                "Ask the user for Da/Net, then run Bot#1/Tester/Bot#2 before DevOps or external write."
                if pre_human_gate
                else "Ask the user for Da/Net before DevOps or external write."
            )
        ],
        "confidence": 1.0,
        "approved_action": "needs_human",
        "pre_human_gate": pre_human_gate,
        "live_dual_deferred_until_yes": live_dual_deferred,
    }


def build_process_performance(
    *,
    duration_ms: int,
    route_audit: dict[str, Any],
    verdict: dict[str, Any],
) -> dict[str, Any]:
    raw_audit = route_audit.get("raw") or {}
    review_cycles = verdict.get("review_cycles") or []
    live_review_latency_ms = 0
    live_review_calls = 0
    live_review_http_timing = {
        "request_count": 0,
        "total": 0,
        "end_to_end_total": 0,
        "time_to_headers": 0,
        "read_body": 0,
        "failed_attempt_count": 0,
        "failed_attempt_total": 0,
    }
    live_review_completion_budget = {
        "cap_hit_count": 0,
        "cap_hit_roles": [],
        "over_budget_count": 0,
        "over_budget_roles": [],
    }
    for cycle in review_cycles:
        latencies = cycle.get("latency_ms") or {}
        for value in latencies.values():
            if isinstance(value, int):
                live_review_latency_ms += value
        http_timings = cycle.get("http_timing_ms") or {}
        if isinstance(http_timings, dict):
            for timing in http_timings.values():
                if not isinstance(timing, dict) or not timing:
                    continue
                live_review_http_timing["request_count"] += 1
                for key in [
                    "total",
                    "end_to_end_total",
                    "time_to_headers",
                    "read_body",
                    "failed_attempt_count",
                    "failed_attempt_total",
                ]:
                    value = timing.get(key)
                    if isinstance(value, int):
                        live_review_http_timing[key] += value
                if "end_to_end_total" not in timing:
                    live_review_http_timing["end_to_end_total"] += int(timing.get("total") or 0)
        budgets = cycle.get("completion_budget") or {}
        if isinstance(budgets, dict):
            for role, budget in budgets.items():
                if not isinstance(budget, dict) or not budget.get("hit_cap"):
                    if isinstance(budget, dict) and budget.get("over_budget"):
                        live_review_completion_budget["over_budget_count"] += 1
                        live_review_completion_budget["over_budget_roles"].append(str(role))
                    continue
                live_review_completion_budget["cap_hit_count"] += 1
                live_review_completion_budget["cap_hit_roles"].append(str(role))
                if budget.get("over_budget"):
                    live_review_completion_budget["over_budget_count"] += 1
                    live_review_completion_budget["over_budget_roles"].append(str(role))
        live_review_calls += 2
        if cycle.get("bot1_self_check"):
            live_review_calls += 1
        if cycle.get("bot2_repair_attempted"):
            live_review_calls += 1
    return {
        "duration_ms": duration_ms,
        "route_audit": {
            "enabled": bool(route_audit),
            "skipped": bool(raw_audit.get("audit_skipped")),
            "cache_hit": bool(raw_audit.get("cache_hit")),
            "latency_ms": int(raw_audit.get("latency_ms") or 0),
            "model": raw_audit.get("model", ""),
            "status": route_audit.get("status", ""),
            "source": route_audit.get("source", ""),
        },
        "live_review": {
            "cycle_count": len(review_cycles),
            "llm_call_count": live_review_calls,
            "latency_ms": live_review_latency_ms,
            "http_timing_ms": live_review_http_timing,
            "completion_budget": live_review_completion_budget,
            "review_policy": verdict.get("review_policy", {}),
        },
    }


def emit_human_gate(
    *,
    process_id_value: str,
    supervisor_task_id: str,
    route: dict[str, Any],
    bot2_session_id: str,
    verdict: dict[str, Any],
    notify_telegram: bool,
    notification_dry_run: bool,
    process_store: Path | str | None,
    supervisor_store: Path | str | None,
) -> dict[str, Any]:
    task_state = get_task(supervisor_task_id, store_path=supervisor_store)
    safe_task_state = redact_payload(task_state)
    safe_verdict = redact_payload(verdict)
    create_human_escalation(safe_task_state, bot2_session_id, safe_verdict, store_path=supervisor_store)
    human_message = escalation_text(safe_task_state, safe_verdict)
    human_notification = build_human_notification_payload(
        process_id=process_id_value,
        supervisor_task_id=supervisor_task_id,
        task=safe_task_state,
        route=route,
        bot2_session_id=bot2_session_id,
        verdict=safe_verdict,
    )
    notification_delivery = dispatch_human_notification(
        human_notification,
        telegram=notify_telegram,
        dry_run=notification_dry_run,
    )
    add_supervisor_event(
        supervisor_task_id,
        "human_escalation",
        {"message": human_message, "notification": human_notification, "delivery": notification_delivery},
        store_path=supervisor_store,
    )
    add_process_event(
        process_id_value,
        "human_notification",
        {"notification": human_notification, "delivery": notification_delivery},
        store_path=process_store,
    )
    add_assignment(
        process_id_value,
        "supervisor",
        "human_decision",
        "waiting",
        {"message": human_message, "notification_event": "human_notification", "delivery": notification_delivery},
        store_path=process_store,
    )
    return {
        "human_message": human_message,
        "human_notification": human_notification,
        "notification_delivery": notification_delivery,
    }


def latest_bot2_verdict_from_process(data: dict[str, Any]) -> dict[str, Any]:
    assignments = list(data.get("assignments") or [])
    events = list(data.get("events") or [])
    bot2_event = latest_event(events, "bot2_verdict")
    bot2_assignment = latest_assignment(assignments, "bot2")
    verdict = (
        (bot2_event.get("payload") or {}).get("verdict")
        or (bot2_assignment.get("output") or {}).get("verdict")
        or {}
    )
    return verdict if isinstance(verdict, dict) else {}


def human_decision_next_action(
    *,
    choice: str,
    process_id_value: str,
    supervisor_task_id: str,
    verdict: dict[str, Any],
    route: dict[str, Any],
) -> dict[str, Any]:
    normalized = choice.lower().strip()
    if normalized == "yes":
        return {
            "action": "return_to_bot1_with_bot2_fixes",
            "status": "return_to_bot1",
            "target_worker": "bot1",
            "target_phase": "revision",
            "process_id": process_id_value,
            "supervisor_task_id": supervisor_task_id,
            "bot2_status": verdict.get("status", ""),
            "bot2_summary": verdict.get("summary", ""),
            "required_fixes": verdict.get("required_fixes", []),
            "risks": verdict.get("risks", []),
            "resume_hint": (
                "Resume Bot#1 with the Bot#2 required_fixes package, then run Tester/Bot#2 again "
                "before DevOps or external writes."
            ),
        }
    return {
        "action": "accept_bot1_user_override",
        "status": "accepted_by_user_override",
        "target_worker": "supervisor",
        "target_phase": "final_decision",
        "process_id": process_id_value,
        "supervisor_task_id": supervisor_task_id,
        "bot2_status": verdict.get("status", ""),
        "bot2_summary": verdict.get("summary", ""),
        "devops_allowed_after_override": "devops_if_approved" in route.get("process_plan", []),
        "resume_hint": "Keep Bot#1 result as final by explicit user override and continue only with route/tool gates.",
    }


def decide_process(args: argparse.Namespace) -> dict[str, Any]:
    details = process_details(
        args.process_id,
        store_path=args.process_store,
        supervisor_store_path=args.supervisor_store,
    )
    status = str(details.get("status") or "")
    if status != "awaiting_human_decision":
        raise SystemExit(f"process is not awaiting a human decision: {args.process_id} status={status}")

    supervisor_task_id = str(details.get("supervisor_task_id") or "")
    decision = record_human_decision(
        supervisor_task_id,
        args.choice,
        args.reason or "",
        store_path=args.supervisor_store,
    )
    route = details.get("router") or {}
    verdict = latest_bot2_verdict_from_process(details)
    next_action = human_decision_next_action(
        choice=str(decision.get("choice") or args.choice),
        process_id_value=args.process_id,
        supervisor_task_id=supervisor_task_id,
        verdict=verdict,
        route=route,
    )
    next_status = str(decision.get("status") or next_action["status"])
    next_phase = "bot1_revision" if next_status == "return_to_bot1" else "final_decision"

    event_payload = {"decision": decision, "next_action": next_action}
    add_process_event(args.process_id, "human_decision", event_payload, store_path=args.process_store)
    add_process_event(args.process_id, "process_next_action", next_action, store_path=args.process_store)
    add_assignment(
        args.process_id,
        "supervisor",
        "human_decision",
        "completed",
        event_payload,
        store_path=args.process_store,
    )
    if next_status == "return_to_bot1":
        add_assignment(
            args.process_id,
            "bot1",
            "revision",
            "pending",
            {
                "source": "human_agreed_with_bot2",
                "required_fixes": next_action.get("required_fixes", []),
                "risks": next_action.get("risks", []),
            },
            store_path=args.process_store,
        )
    update_process(args.process_id, status=next_status, current_phase=next_phase, store_path=args.process_store)
    return {
        "process_id": args.process_id,
        "supervisor_task_id": supervisor_task_id,
        "decision": decision,
        "status": next_status,
        "next_action": next_action,
    }


def process_was_dry(details: dict[str, Any]) -> bool:
    supervisor = details.get("supervisor") or {}
    previous_bot1 = str(supervisor.get("bot1_result") or "")
    summary = details.get("summary") or {}
    bot2 = summary.get("bot2") or {}
    bot2_session_id = str(bot2.get("session_id") or "")
    return (
        previous_bot1.startswith("Bot#1 dry-run result")
        or bot2_session_id.endswith("-bot2-dry")
        or bot2_session_id.endswith("-route-policy")
        or bot2_session_id.endswith("-route-policy-dry")
    )


def continue_mode(args: argparse.Namespace, details: dict[str, Any]) -> str:
    mode = str(getattr(args, "mode", "auto") or "auto").lower()
    if mode in {"dry", "live"}:
        return mode
    return "dry" if process_was_dry(details) else "live"


def dry_revision_verdict(prior_verdict: dict[str, Any]) -> dict[str, Any]:
    previous_cycles = list(prior_verdict.get("review_cycles") or [])
    previous_rounds = [int(cycle.get("round") or 0) for cycle in previous_cycles if isinstance(cycle, dict)]
    round_no = max(previous_rounds or [0]) + 1
    verdict = dry_verdict("APPROVE_WITH_EVIDENCE")
    verdict.update(
        {
            "summary": "Dry Bot#2 verdict after human-approved Bot#1 revision.",
            "evidence_checked": ["dry-run Bot#1 revision package", "human YES decision"],
            "review_cycles": previous_cycles
            + [
                {
                    "round": round_no,
                    "human_continue": True,
                    "bot1_self_check": True,
                    "bot2_status": "APPROVE_WITH_EVIDENCE",
                    "bot2_summary": "Dry Bot#2 verdict after human-approved Bot#1 revision.",
                    "required_fixes": [],
                    "risks": [],
                    "fix_closure_checklist": [
                        {
                            "required_fix": str(fix),
                            "status": "claimed_closed_by_bot1_self_check",
                            "evidence": f"Dry Bot#1 human-approved revision round {round_no}",
                        }
                        for fix in (prior_verdict.get("required_fixes") or [])
                    ],
                }
            ],
        }
    )
    verdict["fix_closure_checklist"] = verdict["review_cycles"][-1]["fix_closure_checklist"]
    return verdict


def continue_process(args: argparse.Namespace) -> dict[str, Any]:
    process_started_at = time.perf_counter()
    details = process_details(
        args.process_id,
        store_path=args.process_store,
        supervisor_store_path=args.supervisor_store,
    )
    status = str(details.get("status") or "")
    if status != "return_to_bot1":
        raise SystemExit(f"process is not ready for Bot#1 continuation: {args.process_id} status={status}")

    events = list(details.get("events") or [])
    next_action = (latest_event(events, "process_next_action").get("payload") or {})
    if next_action.get("action") != "return_to_bot1_with_bot2_fixes":
        raise SystemExit(f"process has no Bot#1 revision action: {args.process_id}")

    supervisor_task_id = str(details.get("supervisor_task_id") or "")
    supervisor = details.get("supervisor") or task_details(supervisor_task_id, store_path=args.supervisor_store)
    route = details.get("router") or {}
    skill_context = route.get("skill_context") or {}
    task = str(details.get("task") or "")
    acceptance = str(details.get("acceptance") or "")
    previous_answer = str(supervisor.get("bot1_result") or "")
    prior_verdict = latest_bot2_verdict_from_process(details)
    mode = continue_mode(args, details)

    add_process_event(
        args.process_id,
        "bot1_revision_started",
        {"mode": mode, "next_action": next_action, "prior_required_fixes": prior_verdict.get("required_fixes", [])},
        store_path=args.process_store,
    )
    add_assignment(
        args.process_id,
        "bot1",
        "revision",
        "running",
        {"mode": mode, "source": "human_agreed_with_bot2", "required_fixes": prior_verdict.get("required_fixes", [])},
        store_path=args.process_store,
    )
    update_task(supervisor_task_id, status="running", store_path=args.supervisor_store)
    update_process(args.process_id, status="running", current_phase="bot1_revision", store_path=args.process_store)

    try:
        if mode == "live":
            bot1_result, bot2_session_id, verdict, report_path = live_bot1_revision_result(
                task,
                acceptance,
                previous_answer=previous_answer,
                prior_verdict=prior_verdict,
                bot1_model=args.bot1_model,
                bot2_model=args.bot2_model,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
                skill_context=skill_context,
                route=route,
            )
        else:
            bot1_result = dry_bot1_revision_result(task, acceptance, previous_answer, prior_verdict, route)
            bot2_session_id = f"{args.process_id}-bot2-continue-dry"
            verdict = dry_revision_verdict(prior_verdict)
            report_path = ""
    except Exception as exc:
        failure = {"mode": mode, "error": f"{type(exc).__name__}: {exc}"}
        add_process_event(args.process_id, "bot1_revision_failed", failure, store_path=args.process_store)
        add_assignment(args.process_id, "bot1", "revision", "failed", failure, store_path=args.process_store)
        update_task(supervisor_task_id, status="failed", store_path=args.supervisor_store)
        update_process(args.process_id, status="failed", current_phase="bot1_revision_failed", store_path=args.process_store)
        raise

    evidence = bot1_result
    update_task(supervisor_task_id, bot1_result=bot1_result, evidence=evidence, store_path=args.supervisor_store)
    add_assignment(
        args.process_id,
        "bot1",
        "revision",
        "completed",
        {
            "mode": mode,
            "result_chars": len(bot1_result),
            "report_path": report_path,
            "review_cycle_count": len(verdict.get("review_cycles") or []),
            "skills": skill_context_for_role(skill_context, "bot1"),
        },
        store_path=args.process_store,
    )
    add_role_run(
        supervisor_task_id,
        "bot1",
        "completed",
        "Bot#1 revision completed after human YES.",
        {"process_id": args.process_id, "mode": mode, "report_path": report_path},
        store_path=args.supervisor_store,
    )
    add_process_event(
        args.process_id,
        "bot1_revision",
        {"mode": mode, "result_chars": len(bot1_result), "report_path": report_path},
        store_path=args.process_store,
    )

    if route_requires_tester(route):
        add_assignment(
            args.process_id,
            "tester",
            "verification",
            "completed",
            {"evidence_chars": len(evidence), "source": "bot1_revision", "skills": skill_context_for_role(skill_context, "tester")},
            store_path=args.process_store,
        )
        add_role_run(
            supervisor_task_id,
            "tester",
            "completed",
            "Tester evidence package completed after Bot#1 revision.",
            {"process_id": args.process_id},
            store_path=args.supervisor_store,
        )

    link_bot2(supervisor_task_id, bot2_session_id, verdict, store_path=args.supervisor_store)
    final_status = supervisor_status_for_verdict(verdict)
    add_assignment(
        args.process_id,
        "bot2",
        "quality_gate",
        "completed",
        {"session_id": bot2_session_id, "verdict": verdict, "skills": skill_context_for_role(skill_context, "bot2")},
        store_path=args.process_store,
    )
    add_role_run(
        supervisor_task_id,
        "bot2",
        "completed",
        f"Bot#2 verdict after Bot#1 revision: {verdict.get('status')}",
        {"process_id": args.process_id, "verdict": verdict},
        store_path=args.supervisor_store,
    )
    add_process_event(
        args.process_id,
        "bot2_verdict",
        {"bot2_session_id": bot2_session_id, "verdict": verdict, "supervisor_status": final_status, "after_human_continue": True},
        store_path=args.process_store,
    )
    if verdict.get("review_cycles"):
        add_process_event(
            args.process_id,
            "bot_review_cycles",
            {
                "bot2_session_id": bot2_session_id,
                "review_cycles": verdict.get("review_cycles", []),
                "fix_closure_checklist": verdict.get("fix_closure_checklist", []),
                "after_human_continue": True,
            },
            store_path=args.process_store,
        )
    if verdict.get("fix_closure_checklist"):
        add_process_event(
            args.process_id,
            "bot1_self_check",
            {
                "round": (verdict.get("review_cycles") or [{}])[-1].get("round"),
                "fix_closure_checklist": verdict.get("fix_closure_checklist", []),
                "after_human_continue": True,
            },
            store_path=args.process_store,
        )

    update_task(supervisor_task_id, status=final_status, store_path=args.supervisor_store)
    human_gate = {"human_message": "", "human_notification": {}, "notification_delivery": {}}
    if final_status == "awaiting_human_decision":
        human_gate = emit_human_gate(
            process_id_value=args.process_id,
            supervisor_task_id=supervisor_task_id,
            route=route,
            bot2_session_id=bot2_session_id,
            verdict=verdict,
            notify_telegram=args.notify_telegram,
            notification_dry_run=args.notification_dry_run,
            process_store=args.process_store,
            supervisor_store=args.supervisor_store,
        )

    if final_status in {"approved", "approved_refusal"}:
        final_action_name = "completed_after_bot1_revision"
    elif final_status == "awaiting_human_decision":
        final_action_name = "await_human_after_bot1_revision"
    else:
        final_action_name = "stop_after_bot1_revision"
    final_next_action = {
        "action": final_action_name,
        "status": final_status,
        "target_worker": "supervisor" if final_status == "awaiting_human_decision" else "",
        "process_id": args.process_id,
        "bot2_status": verdict.get("status", ""),
        "bot2_summary": verdict.get("summary", ""),
    }
    add_process_event(args.process_id, "process_next_action", final_next_action, store_path=args.process_store)
    performance = build_process_performance(duration_ms=elapsed_ms(process_started_at), route_audit={}, verdict=verdict)
    add_process_event(args.process_id, "process_performance", performance, store_path=args.process_store)
    update_process(args.process_id, status=final_status, current_phase=final_status, store_path=args.process_store)
    return {
        "process_id": args.process_id,
        "supervisor_task_id": supervisor_task_id,
        "status": final_status,
        "mode": mode,
        "bot2_session_id": bot2_session_id,
        "bot2_verdict": verdict,
        "report_path": report_path,
        "human_message": human_gate.get("human_message", ""),
        "human_notification": human_gate.get("human_notification", {}),
        "notification_delivery": human_gate.get("notification_delivery", {}),
        "next_action": final_next_action,
        "performance": performance,
    }


def run_process(args: argparse.Namespace) -> dict[str, Any]:
    process_started_at = time.perf_counter()
    task = args.task.strip()
    acceptance = args.acceptance.strip()
    initial_route = classify_task(task)
    route_audit = route_audit_from_args(args, task, initial_route)
    route = apply_classification_audit(initial_route, route_audit) if route_audit else initial_route
    skill_context = build_route_skill_context(route)
    route = dict(route)
    route["skill_context"] = skill_context
    supervisor_created = create_task(task, store_path=args.supervisor_store)
    supervisor_task_id = supervisor_created["task_id"]
    pid = create_process_run(
        task=task,
        acceptance=acceptance,
        route=route,
        supervisor_task_id=supervisor_task_id,
        store_path=args.process_store,
    )
    add_process_event(pid, "routed", route, store_path=args.process_store)
    add_process_event(pid, "skill_context_selected", skill_context, store_path=args.process_store)
    add_assignment(pid, "router", "intake", "completed", route, store_path=args.process_store)
    add_assignment(pid, "skill_index", "context_selection", "completed", skill_context, store_path=args.process_store)
    add_assignment(pid, "supervisor", "create_contract", "completed", supervisor_created, store_path=args.process_store)
    add_supervisor_event(
        supervisor_task_id,
        "process_router_attached",
        {"process_id": pid, "route": route},
        store_path=args.supervisor_store,
    )
    update_process(pid, status="running", current_phase="bot1", store_path=args.process_store)
    update_task(supervisor_task_id, status="running", store_path=args.supervisor_store)
    if route_audit:
        classification_audit = route.get("classification_audit", {})
        raw_audit = classification_audit.get("raw") or {}
        audit_skipped = bool(raw_audit.get("audit_skipped"))
        cache_hit = bool(raw_audit.get("cache_hit"))
        audit_worker = "route_audit_policy" if audit_skipped else "bot2_route_audit_cache" if cache_hit else "bot2_route_audit"
        audit_status = "skipped" if audit_skipped else "completed"
        audit_message = (
            "Live Bot#2 classification audit skipped by low-risk fast-path."
            if audit_skipped
            else "Bot#2 classification audit reused from cache."
            if cache_hit
            else "Bot#2 classification audit completed."
        )
        add_process_event(
            pid,
            "classification_audit",
            {"initial_route": initial_route, "route": route, "audit": classification_audit},
            store_path=args.process_store,
        )
        add_assignment(
            pid,
            audit_worker,
            "classification",
            audit_status,
            classification_audit,
            store_path=args.process_store,
        )
        add_role_run(
            supervisor_task_id,
            audit_worker,
            audit_status,
            audit_message,
            {"process_id": pid, "audit": classification_audit},
            store_path=args.supervisor_store,
        )

    bot2_session_id = ""
    verdict: dict[str, Any] = {}
    report_path = ""
    human_message = ""
    human_notification: dict[str, Any] = {}
    notification_delivery: dict[str, Any] = {}
    pre_human_gate = bool(args.live_dual and route.get("human_gate_required"))

    if route_requires_bot1(route):
        if pre_human_gate:
            bot1_result = pre_human_gate_result(
                task,
                acceptance,
                route,
                live_dual_requested=bool(args.live_dual),
            )
            bot2_session_id = f"{pid}-route-policy-pre-gate"
            verdict = route_policy_verdict(pre_human_gate=True, live_dual_deferred=True)
            evidence = bot1_result
            update_task(supervisor_task_id, bot1_result=bot1_result, evidence=evidence, store_path=args.supervisor_store)
            add_process_event(
                pid,
                "pre_human_gate",
                {
                    "reason": "route_human_gate_required",
                    "live_dual_deferred_until_yes": True,
                    "bot2_session_id": bot2_session_id,
                },
                store_path=args.process_store,
            )
            add_assignment(
                pid,
                "supervisor",
                "pre_human_gate",
                "waiting",
                {
                    "bot2_session_id": bot2_session_id,
                    "verdict": verdict,
                    "next_step": "ask_human_before_live_bot1_bot2",
                },
                store_path=args.process_store,
            )
            add_role_run(
                supervisor_task_id,
                "supervisor",
                "waiting",
                "Pre-human gate blocked live Bot#1/Bot#2 until explicit human decision.",
                {"process_id": pid, "verdict": verdict},
                store_path=args.supervisor_store,
            )
        elif args.live_dual and route_requires_bot2(route):
            bot1_result, bot2_session_id, verdict, report_path = live_dual_result(
                task,
                acceptance,
                bot1_model=args.bot1_model,
                bot2_model=args.bot2_model,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
                skill_context=skill_context,
                route=route,
            )
        elif args.live_dual:
            bot1_result, bot2_session_id, report_path = live_bot1_result(
                task,
                acceptance,
                bot1_model=args.bot1_model,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
                skill_context=skill_context_for_role(skill_context, "bot1"),
                route=route,
            )
        else:
            bot1_result = args.bot1_result or dry_bot1_result(task, acceptance, route)
            if route_requires_bot2(route):
                bot2_session_id = f"{pid}-bot2-dry"
                verdict = configured_bot2_verdict(args)
        if not pre_human_gate:
            evidence = args.evidence or bot1_result
            update_task(supervisor_task_id, bot1_result=bot1_result, evidence=evidence, store_path=args.supervisor_store)
            add_assignment(
                pid,
                "bot1",
                "execution",
                "completed",
                {
                    "result_chars": len(bot1_result),
                    "report_path": report_path,
                    "review_cycle_count": len(verdict.get("review_cycles") or []),
                    "skills": skill_context_for_role(skill_context, "bot1"),
                },
                store_path=args.process_store,
            )
            add_role_run(
                supervisor_task_id,
                "bot1",
                "completed",
                "Bot#1 process completed.",
                {"process_id": pid},
                store_path=args.supervisor_store,
            )
            if route_requires_tester(route):
                add_assignment(
                    pid,
                    "tester",
                    "verification",
                    "completed",
                    {"evidence_chars": len(evidence), "skills": skill_context_for_role(skill_context, "tester")},
                    store_path=args.process_store,
                )
                add_role_run(
                    supervisor_task_id,
                    "tester",
                    "completed",
                    "Tester evidence package completed.",
                    {"process_id": pid},
                    store_path=args.supervisor_store,
                )
    else:
        bot1_result = args.bot1_result or dry_bot1_result(task, acceptance, route)
        evidence = bot1_result
        update_task(supervisor_task_id, bot1_result=bot1_result, evidence=evidence, store_path=args.supervisor_store)
        add_process_event(pid, "no_llm_route_completed", {"route": route}, store_path=args.process_store)

    needs_bot2 = route_requires_bot2(route)
    final_status = supervisor_status_for_verdict(verdict) if needs_bot2 else "approved"
    if route.get("human_gate_required") and final_status in {"approved", "approved_refusal"}:
        verdict = route_policy_verdict()
        final_status = "awaiting_human_decision"
        if needs_bot2 and not bot2_session_id:
            bot2_session_id = f"{pid}-route-policy"

    if needs_bot2:
        link_bot2(supervisor_task_id, bot2_session_id, verdict, store_path=args.supervisor_store)
        add_assignment(
            pid,
            "bot2",
            "quality_gate",
            "completed",
            {
                "session_id": bot2_session_id,
                "verdict": verdict,
                "skills": skill_context_for_role(skill_context, "bot2"),
            },
            store_path=args.process_store,
        )
        add_role_run(
            supervisor_task_id,
            "bot2",
            "completed",
            f"Bot#2 verdict: {verdict.get('status')}",
            {"process_id": pid, "verdict": verdict},
            store_path=args.supervisor_store,
        )
        add_process_event(
            pid,
            "bot2_verdict",
            {"bot2_session_id": bot2_session_id, "verdict": verdict, "supervisor_status": final_status},
            store_path=args.process_store,
        )
        if verdict.get("review_cycles"):
            add_process_event(
                pid,
                "bot_review_cycles",
                {
                    "bot2_session_id": bot2_session_id,
                    "review_cycles": verdict.get("review_cycles", []),
                    "fix_closure_checklist": verdict.get("fix_closure_checklist", []),
                },
                store_path=args.process_store,
            )
            for cycle in verdict.get("review_cycles", []):
                if cycle.get("bot1_self_check"):
                    add_process_event(
                        pid,
                        "bot1_self_check",
                        {
                            "round": cycle.get("round"),
                            "fix_closure_checklist": cycle.get("fix_closure_checklist", []),
                        },
                        store_path=args.process_store,
                    )
                    add_role_run(
                        supervisor_task_id,
                        "bot1_self_check",
                        "completed",
                        f"Bot#1 self-check round {cycle.get('round')}",
                        {"process_id": pid, "fix_closure_checklist": cycle.get("fix_closure_checklist", [])},
                        store_path=args.supervisor_store,
                    )
                if cycle.get("bot2_repair_attempted"):
                    add_process_event(
                        pid,
                        "bot2_json_repair",
                        {
                            "round": cycle.get("round"),
                            "repair_status": cycle.get("bot2_repair_status", ""),
                        },
                        store_path=args.process_store,
                    )
            if verdict.get("loop_status") == "max_review_cycles_reached":
                add_process_event(
                    pid,
                    "repair_loop_exhausted",
                    {
                        "max_review_cycles": int(
                            (verdict.get("review_policy") or {}).get("effective_max_cycles") or MAX_BOT_REVIEW_CYCLES
                        ),
                        "review_policy": verdict.get("review_policy", {}),
                        "verdict": verdict,
                    },
                    store_path=args.process_store,
                )

    update_task(supervisor_task_id, status=final_status, store_path=args.supervisor_store)
    if final_status == "awaiting_human_decision":
        human_gate = emit_human_gate(
            process_id_value=pid,
            supervisor_task_id=supervisor_task_id,
            route=route,
            bot2_session_id=bot2_session_id,
            verdict=verdict,
            notify_telegram=args.notify_telegram,
            notification_dry_run=args.notification_dry_run,
            process_store=args.process_store,
            supervisor_store=args.supervisor_store,
        )
        human_message = str(human_gate.get("human_message") or "")
        human_notification = human_gate.get("human_notification") or {}
        notification_delivery = human_gate.get("notification_delivery") or {}

    performance = build_process_performance(
        duration_ms=elapsed_ms(process_started_at),
        route_audit=route.get("classification_audit", {}) if route_audit else {},
        verdict=verdict,
    )
    add_process_event(pid, "process_performance", performance, store_path=args.process_store)
    update_process(pid, status=final_status, current_phase=final_status, store_path=args.process_store)
    return {
        "process_id": pid,
        "supervisor_task_id": supervisor_task_id,
        "status": final_status,
        "route": route,
        "bot2_session_id": bot2_session_id,
        "bot2_verdict": verdict,
        "report_path": report_path,
        "human_message": human_message,
        "human_notification": human_notification,
        "notification_delivery": notification_delivery,
        "performance": performance,
    }


def process_event_rows(pid: str, *, limit: int = 0, store_path: Path | str | None = None) -> list[dict[str, Any]]:
    limit_clause = "LIMIT ?" if limit > 0 else ""
    params: tuple[Any, ...] = (pid, limit) if limit > 0 else (pid,)
    with connect(store_path) as con:
        rows = con.execute(
            f"""
            SELECT created_at, event_type, payload_json
            FROM process_events
            WHERE process_id=?
            ORDER BY id DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
    events = [dict(row) | {"payload": json.loads(row["payload_json"])} for row in rows]
    for event in events:
        event.pop("payload_json", None)
    return list(reversed(events))


def latest_assignment(assignments: list[dict[str, Any]], worker: str) -> dict[str, Any]:
    for assignment in reversed(assignments):
        if assignment.get("worker") == worker:
            return assignment
    return {}


def latest_event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("event_type") == event_type:
            return event
    return {}


def blocked_reason(status: str, events: list[dict[str, Any]], assignments: list[dict[str, Any]]) -> str:
    if status != "blocked":
        return ""
    event = latest_event(events, "bot2_verdict")
    verdict = (event.get("payload") or {}).get("verdict") or latest_assignment(assignments, "bot2").get("output", {}).get("verdict") or {}
    risks = verdict.get("risks") or verdict.get("required_fixes") or []
    if isinstance(risks, list) and risks:
        return "; ".join(str(item) for item in risks)
    return str(verdict.get("summary") or "blocked without detailed reason")


def process_summary(
    data: dict[str, Any],
    *,
    supervisor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assignments = list(data.get("assignments") or [])
    events = list(data.get("events") or [])
    route = data.get("router") or {}
    skill_context = route.get("skill_context") or {}
    bot2_assignment = latest_assignment(assignments, "bot2")
    bot2_event = latest_event(events, "bot2_verdict")
    human_event = latest_event(events, "human_notification")
    performance_event = latest_event(events, "process_performance")
    next_action_event = latest_event(events, "process_next_action")
    bot2_verdict = (
        (bot2_event.get("payload") or {}).get("verdict")
        or (bot2_assignment.get("output") or {}).get("verdict")
        or {}
    )
    notification_payload = human_event.get("payload") or {}
    notification_delivery = notification_payload.get("delivery") or {}
    human_decision: dict[str, Any] = {}
    if supervisor:
        escalations = supervisor.get("human_escalations") or []
        if escalations:
            latest = escalations[-1]
            human_decision = {
                "required": True,
                "status": latest.get("status", ""),
                "choice": latest.get("choice"),
                "meaning": latest.get("meaning"),
                "reason": latest.get("reason", ""),
                "bot2_session_id": latest.get("bot2_session_id", ""),
                "yes_meaning": YES_MEANING,
                "no_meaning": NO_MEANING,
            }
    if not human_decision and human_event:
        human_decision = {
            "required": True,
            "status": "awaiting_decision",
            "choice": None,
            "yes_meaning": YES_MEANING,
            "no_meaning": NO_MEANING,
        }

    status = str(data.get("status") or "")
    bot2_required = route_requires_bot2(route)
    actors = [str(item.get("worker") or "") for item in assignments if item.get("worker")]
    last_event = events[-1] if events else {}

    return {
        "process_id": data.get("id", ""),
        "supervisor_task_id": data.get("supervisor_task_id", ""),
        "status": status,
        "current_phase": data.get("current_phase", ""),
        "waiting_on": "human" if status == "awaiting_human_decision" else "",
        "blocked_reason": blocked_reason(status, events, assignments),
        "supervisor_available": supervisor is not None,
        "task_level": route.get("task_level", ""),
        "task_type": route.get("task_type", ""),
        "risk_level": route.get("risk_level", ""),
        "actors": actors,
        "actor_runs": [
            {
                "worker": item.get("worker", ""),
                "phase": item.get("phase", ""),
                "status": item.get("status", ""),
            }
            for item in assignments
        ],
        "route": {
            "task_level": route.get("task_level", ""),
            "task_type": route.get("task_type", ""),
            "risk_level": route.get("risk_level", ""),
            "review_required": bool(route.get("review_required")),
            "human_gate_required": bool(route.get("human_gate_required")),
            "classification_audit": route.get("classification_audit", {}),
        },
        "skills": {
            "status": skill_context.get("status", ""),
            "selection_policy": skill_context.get("selection_policy", ""),
            "task_tags": skill_context.get("task_tags", []),
            "selected": [item.get("name", "") for item in skill_context.get("selected_skills", [])],
            "gated": [item.get("name", "") for item in skill_context.get("gated_skills", [])],
            "roles": {
                role: [item.get("name", "") for item in skills]
                for role, skills in (skill_context.get("roles") or {}).items()
            },
            "gated_roles": {
                role: [item.get("name", "") for item in skills]
                for role, skills in (skill_context.get("gated_roles") or {}).items()
            },
            "runtime_contract": skill_context.get("runtime_contract", {}),
        },
        "bot2": {
            "required": bot2_required,
            "session_id": (bot2_event.get("payload") or {}).get("bot2_session_id")
            or (bot2_assignment.get("output") or {}).get("session_id", ""),
            "status": bot2_verdict.get("status", ""),
            "summary": bot2_verdict.get("summary", ""),
            "risks": bot2_verdict.get("risks", []),
            "repair_attempted": bool(bot2_verdict.get("repair_attempted")),
            "repair_status": bot2_verdict.get("repair_status", ""),
            "review_cycle_count": len(bot2_verdict.get("review_cycles") or []),
        },
        "human_decision": human_decision or {
            "required": False,
            "status": "",
            "choice": None,
            "yes_meaning": YES_MEANING,
            "no_meaning": NO_MEANING,
        },
        "notification": {
            "sent": bool(notification_delivery.get("telegram_delivered")),
            "mode": notification_delivery.get("mode", ""),
            "provider": "telegram" if notification_delivery.get("telegram_requested") else "",
        },
        "next_action": next_action_event.get("payload") or {},
        "reports": {
            "dual_bot_report": next(
                (
                    value
                    for value in [
                        (latest_event(events, "report").get("payload") or {}).get("report_path"),
                        (latest_assignment(assignments, "bot1").get("output") or {}).get("report_path"),
                    ]
                    if value
                ),
                "",
            )
        },
        "performance": performance_event.get("payload") or {},
        "event_count": len(events),
        "assignment_count": len(assignments),
        "last_event_type": last_event.get("event_type", ""),
        "last_event_at": last_event.get("created_at", ""),
    }


def process_timeline(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for event in data.get("events") or []:
        items.append(
            {
                "created_at": event.get("created_at", ""),
                "kind": "event",
                "event_type": event.get("event_type", ""),
                "actor": "",
                "phase": "",
                "status": "",
            }
        )
    for assignment in data.get("assignments") or []:
        items.append(
            {
                "created_at": assignment.get("created_at", ""),
                "kind": "assignment",
                "event_type": "",
                "actor": assignment.get("worker", ""),
                "phase": assignment.get("phase", ""),
                "status": assignment.get("status", ""),
            }
        )
    return sorted(items, key=lambda item: str(item.get("created_at") or ""))


def process_details(
    pid: str,
    *,
    store_path: Path | str | None = None,
    supervisor_store_path: Path | str | None = None,
) -> dict[str, Any]:
    with connect(store_path) as con:
        run = con.execute("SELECT * FROM process_runs WHERE id=?", (pid,)).fetchone()
        if not run:
            raise SystemExit(f"process run not found: {pid}")
        assignments = con.execute(
            "SELECT created_at, worker, phase, status, output_json FROM process_assignments WHERE process_id=? ORDER BY id",
            (pid,),
        ).fetchall()
    data = dict(run)
    data["router"] = json.loads(data.pop("router_json"))
    data["events"] = process_event_rows(pid, store_path=store_path)
    data["assignments"] = [dict(row) | {"output": json.loads(row["output_json"])} for row in assignments]
    for row in data["assignments"]:
        row.pop("output_json", None)
    supervisor: dict[str, Any] | None = None
    if supervisor_store_path is not None:
        try:
            supervisor = task_details(str(data["supervisor_task_id"]), store_path=supervisor_store_path)
        except SystemExit:
            supervisor = None
    if supervisor:
        data["supervisor"] = supervisor
    data["summary"] = process_summary(data, supervisor=supervisor)
    data["timeline"] = process_timeline(data)
    return redact_payload(data)


def process_transcript(
    pid: str,
    *,
    store_path: Path | str | None = None,
    supervisor_store_path: Path | str | None = None,
) -> dict[str, Any]:
    details = process_details(pid, store_path=store_path, supervisor_store_path=supervisor_store_path)
    supervisor = details.get("supervisor") or {}
    bot2_links = supervisor.get("bot2_links") or []
    bot2_link = bot2_links[-1] if bot2_links else {}
    bot2_verdict = bot2_link.get("verdict") or (details.get("summary") or {}).get("bot2") or {}
    human_escalations = supervisor.get("human_escalations") or []
    human_escalation = human_escalations[-1] if human_escalations else {}
    supervisor_events = supervisor.get("events") or []
    human_event = next((event for event in reversed(supervisor_events) if event.get("event_type") == "human_escalation"), {})
    human_payload = human_event.get("payload") or {}
    process_events = details.get("events") or []
    self_check_entries = []
    for event in process_events:
        if event.get("event_type") != "bot1_self_check":
            continue
        payload = event.get("payload") or {}
        self_check_entries.append(
            {
                "actor": "bot1_self_check",
                "phase": "fix_closure",
                "status": "completed",
                "round": payload.get("round"),
                "content": {
                    "fix_closure_checklist": payload.get("fix_closure_checklist", []),
                },
            }
        )

    transcript = {
        "process_id": details.get("id", ""),
        "supervisor_task_id": details.get("supervisor_task_id", ""),
        "status": details.get("status", ""),
        "route": details.get("router", {}),
        "skill_context": (details.get("router") or {}).get("skill_context", {}),
        "conversation": [
            {
                "actor": "router",
                "phase": "intake",
                "status": "completed",
                "content": details.get("router", {}),
            },
            {
                "actor": "bot1",
                "phase": "execution",
                "status": "completed" if supervisor.get("bot1_result") else "not_started",
                "content": supervisor.get("bot1_result", ""),
            },
            {
                "actor": "tester",
                "phase": "verification",
                "status": "completed" if supervisor.get("evidence") else "not_started",
                "content": supervisor.get("evidence", ""),
            },
            {
                "actor": "bot2",
                "phase": "quality_gate",
                "status": bot2_verdict.get("status", "") or "not_required",
                "session_id": bot2_link.get("bot2_session_id", ""),
                "content": bot2_verdict,
            },
        ]
        + self_check_entries,
        "review_cycles": bot2_verdict.get("review_cycles", []),
        "fix_closure_checklist": bot2_verdict.get("fix_closure_checklist", []),
        "human_gate": {
            "required": bool(human_escalation),
            "status": human_escalation.get("status", ""),
            "choice": human_escalation.get("choice"),
            "meaning": human_escalation.get("meaning"),
            "bot2_session_id": human_escalation.get("bot2_session_id", ""),
            "message": human_payload.get("message", ""),
            "notification": human_payload.get("notification", {}),
            "delivery": human_payload.get("delivery", {}),
            "yes_meaning": YES_MEANING,
            "no_meaning": NO_MEANING,
        },
        "audit": {
            "role_runs": supervisor.get("role_runs", []),
            "process_events": [
                {
                    "created_at": event.get("created_at", ""),
                    "event_type": event.get("event_type", ""),
                }
                for event in process_events
            ],
            "supervisor_events": [
                {
                    "created_at": event.get("created_at", ""),
                    "event_type": event.get("event_type", ""),
                }
                for event in supervisor_events
            ],
        },
    }
    return redact_payload(transcript)


def cmd_route(args: argparse.Namespace) -> None:
    print(json.dumps(classify_task(args.task), ensure_ascii=False, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    print(json.dumps(run_process(args), ensure_ascii=False, indent=2))


def cmd_decide(args: argparse.Namespace) -> None:
    print(json.dumps(decide_process(args), ensure_ascii=False, indent=2))


def cmd_continue(args: argparse.Namespace) -> None:
    print(json.dumps(continue_process(args), ensure_ascii=False, indent=2))


def cmd_show(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            process_details(
                args.process_id,
                store_path=args.process_store,
                supervisor_store_path=args.supervisor_store,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_transcript(args: argparse.Namespace) -> None:
    print(
        json.dumps(
            process_transcript(
                args.process_id,
                store_path=args.process_store,
                supervisor_store_path=args.supervisor_store,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_events(args: argparse.Namespace) -> None:
    for event in process_event_rows(args.process_id, limit=args.limit, store_path=args.process_store):
        print(json.dumps(redact_payload(event), ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes process orchestrator MVP")
    parser.add_argument("--process-store", default=None)
    parser.add_argument("--supervisor-store", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    route = sub.add_parser("route")
    route.add_argument("--task", required=True)
    route.set_defaults(func=cmd_route)

    run = sub.add_parser("run")
    run.add_argument("--task", required=True)
    run.add_argument("--acceptance", default="Result must satisfy the task with concrete evidence and risk notes.")
    run.add_argument("--bot1-result", default="")
    run.add_argument("--evidence", default="")
    run.add_argument("--bot2-status", default="APPROVE", choices=sorted(BOT2_VERDICT_STATUSES))
    run.add_argument("--bot2-verdict-json", default="", help="Use an explicit Bot#2 verdict JSON object in dry-run mode")
    run.add_argument("--bot2-route-audit-json", default="", help="Use an explicit Bot#2 classification audit JSON object before execution")
    run.add_argument("--live-route-audit", action="store_true", help="Ask Bot#2 to audit Router classification before execution")
    run.add_argument(
        "--route-audit-mode",
        choices=["auto", "always"],
        default="auto",
        help="auto skips deterministic low-risk L0/L1 route audits and caches live audit results; always calls Bot#2",
    )
    run.add_argument("--no-route-audit-cache", action="store_true", help="Disable cached Bot#2 route-audit reuse in auto mode")
    run.add_argument("--live-dual", action="store_true")
    run.add_argument("--bot1-model", default="deepseek-v4-flash")
    run.add_argument("--bot2-model", default="gpt-5.3-codex")
    run.add_argument("--timeout", type=int, default=180)
    run.add_argument("--max-tokens", type=int, default=1400)
    run.add_argument("--notify-telegram", action="store_true", help="Send human-gate notification to Telegram via DevLog settings")
    run.add_argument("--notification-dry-run", action="store_true", help="Build and record the notification payload without network delivery")
    run.set_defaults(func=cmd_run)

    decide = sub.add_parser("decide", help="Record human Да/Нет decision and emit the next process action")
    decide.add_argument("process_id")
    decide.add_argument("--choice", required=True, choices=["yes", "no"], help="yes=Да, agree with Bot#2; no=Нет, accept Bot#1")
    decide.add_argument("--reason", default="")
    decide.set_defaults(func=cmd_decide)

    continue_cmd = sub.add_parser("continue", help="Execute the next process action after a human YES decision")
    continue_cmd.add_argument("process_id")
    continue_cmd.add_argument("--mode", choices=["auto", "dry", "live"], default="auto")
    continue_cmd.add_argument("--bot1-model", default="deepseek-v4-flash")
    continue_cmd.add_argument("--bot2-model", default="gpt-5.3-codex")
    continue_cmd.add_argument("--timeout", type=int, default=180)
    continue_cmd.add_argument("--max-tokens", type=int, default=1400)
    continue_cmd.add_argument("--notify-telegram", action="store_true", help="Send a new human-gate notification if Bot#2 still requests changes")
    continue_cmd.add_argument("--notification-dry-run", action="store_true", help="Record any repeated human-gate notification without network delivery")
    continue_cmd.set_defaults(func=cmd_continue)

    show = sub.add_parser("show")
    show.add_argument("process_id")
    show.set_defaults(func=cmd_show)

    transcript = sub.add_parser("transcript", help="Print Bot#1/Bot#2/Supervisor conversation transcript")
    transcript.add_argument("process_id")
    transcript.set_defaults(func=cmd_transcript)

    events = sub.add_parser("events", help="Print process events as JSONL")
    events.add_argument("process_id")
    events.add_argument("--limit", type=int, default=0)
    events.set_defaults(func=cmd_events)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
