#!/usr/bin/env python3
"""Process-oriented Hermes Supervisor MVP."""

from __future__ import annotations

import argparse
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
from task_router import apply_classification_audit, classify_task, parse_classification_audit


PROCESS_STORE_PATH = Path(
    os.environ.get(
        "PROCESS_STORE_PATH",
        "/var/lib/docker/volumes/hermes-data/_data/process_orchestrator_store.db",
    )
)
ROUTE_AUDIT_CACHE_VERSION = "route-audit-v1"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def process_id() -> str:
    return f"proc-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    store = Path(path or PROCESS_STORE_PATH)
    store.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(store)
    con.row_factory = sqlite3.Row
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


def cached_route_audit(
    *,
    cache_key: str,
    store_path: Path | str | None = None,
) -> dict[str, Any]:
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
    original_latency_ms = int(audit.get("latency_ms") or 0)
    audit["source"] = "bot2_live_route_audit_cache"
    audit["cache_hit"] = True
    audit["cached_at"] = row["created_at"]
    audit["cache_hits"] = hits
    audit["original_latency_ms"] = original_latency_ms
    audit["latency_ms"] = 0
    return audit


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


def live_bot1_result(task: str, acceptance: str, *, bot1_model: str, max_tokens: int, timeout: int) -> tuple[str, str, str]:
    import dual_bot_lab as lab

    cfg = lab.bothub_config()
    rid = lab.run_id()
    lab.add_run(rid, task, acceptance, bot1_model, "")
    started_at = time.perf_counter()
    bot1, bot1_raw = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=bot1_model,
        messages=lab.bot1_messages(task, acceptance),
        max_tokens=max_tokens,
        timeout=timeout,
    )
    lab.add_message(
        rid,
        "Bot#1",
        bot1_model,
        bot1,
        {"usage": bot1_raw.get("usage", {}), "latency_ms": elapsed_ms(started_at)},
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
) -> tuple[str, str, dict[str, Any], str]:
    import dual_bot_lab as lab

    cfg = lab.bothub_config()
    rid = lab.run_id()
    lab.add_run(rid, task, acceptance, bot1_model, bot2_model)
    review_cycles: list[dict[str, Any]] = []
    bot1 = ""
    bot2 = ""
    verdict: dict[str, Any] = {}

    for round_no in range(1, MAX_BOT_REVIEW_CYCLES + 1):
        if round_no == 1:
            bot1_messages = lab.bot1_messages(task, acceptance)
            bot1_speaker = "Bot#1"
        else:
            bot1_messages = lab.bot1_revision_messages(task, acceptance, bot1, verdict, round_no - 1)
            bot1_speaker = f"Bot#1-revision-{round_no}"
        bot1_started_at = time.perf_counter()
        bot1, bot1_raw = lab.call_chat(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=bot1_model,
            messages=bot1_messages,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        bot1_latency_ms = elapsed_ms(bot1_started_at)
        lab.add_message(
            rid,
            bot1_speaker,
            bot1_model,
            bot1,
            {"usage": bot1_raw.get("usage", {}), "latency_ms": bot1_latency_ms},
        )

        self_check = ""
        fix_closure_checklist: list[dict[str, str]] = []
        self_check_latency_ms = 0
        self_check_usage: dict[str, Any] = {}
        if round_no > 1:
            self_check_started_at = time.perf_counter()
            self_check, self_check_raw = lab.call_chat(
                base_url=cfg["base_url"],
                api_key=cfg["api_key"],
                model=bot1_model,
                messages=lab.bot1_self_check_messages(task, acceptance, bot1, verdict, round_no),
                max_tokens=max_tokens,
                timeout=timeout,
            )
            self_check_latency_ms = elapsed_ms(self_check_started_at)
            self_check_usage = self_check_raw.get("usage", {})
            lab.add_message(
                rid,
                f"Bot#1-self-check-{round_no}",
                bot1_model,
                self_check,
                {"usage": self_check_usage, "latency_ms": self_check_latency_ms},
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
            messages=lab.bot2_messages(task, acceptance, bot1),
            max_tokens=max_tokens,
            timeout=timeout,
        )
        bot2_latency_ms = elapsed_ms(bot2_started_at)
        bot2_repair_usage: dict[str, Any] = {}
        lab.add_message(
            rid,
            f"Bot#2-{round_no}",
            bot2_model,
            bot2,
            {"usage": bot2_raw.get("usage", {}), "latency_ms": bot2_latency_ms},
        )
        verdict = parse_verdict(bot2)
        bot2_repair_latency_ms = 0
        if verdict.get("status") == INVALID_BOT2_STATUS:
            bot2_repair_started_at = time.perf_counter()
            bot2_repair, bot2_repair_raw = lab.call_chat(
                base_url=cfg["base_url"],
                api_key=cfg["api_key"],
                model=bot2_model,
                messages=lab.bot2_repair_messages(task, acceptance, bot1, bot2),
                max_tokens=max_tokens,
                timeout=timeout,
            )
            bot2_repair_latency_ms = elapsed_ms(bot2_repair_started_at)
            bot2_repair_usage = bot2_repair_raw.get("usage", {})
            lab.add_message(
                rid,
                f"Bot#2-repair-{round_no}",
                bot2_model,
                bot2_repair,
                {"usage": bot2_repair_usage, "latency_ms": bot2_repair_latency_ms},
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

        loop_exhausted = verdict.get("status") == "REQUEST_CHANGES" and round_no == MAX_BOT_REVIEW_CYCLES
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


def route_policy_verdict() -> dict[str, Any]:
    return {
        "status": "NEEDS_HUMAN",
        "summary": "Route policy requires explicit human approval before this action can continue.",
        "risks": ["route_human_gate_required"],
        "required_fixes": ["Ask the user for Da/Net before DevOps or external write."],
        "confidence": 1.0,
        "approved_action": "needs_human",
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
    for cycle in review_cycles:
        latencies = cycle.get("latency_ms") or {}
        for value in latencies.values():
            if isinstance(value, int):
                live_review_latency_ms += value
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
        },
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


def run_process(args: argparse.Namespace) -> dict[str, Any]:
    process_started_at = time.perf_counter()
    task = args.task.strip()
    acceptance = args.acceptance.strip()
    initial_route = classify_task(task)
    route_audit = route_audit_from_args(args, task, initial_route)
    route = apply_classification_audit(initial_route, route_audit) if route_audit else initial_route
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
    add_assignment(pid, "router", "intake", "completed", route, store_path=args.process_store)
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

    if route_requires_bot1(route):
        if args.live_dual and route_requires_bot2(route):
            bot1_result, bot2_session_id, verdict, report_path = live_dual_result(
                task,
                acceptance,
                bot1_model=args.bot1_model,
                bot2_model=args.bot2_model,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
            )
        elif args.live_dual:
            bot1_result, bot2_session_id, report_path = live_bot1_result(
                task,
                acceptance,
                bot1_model=args.bot1_model,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
            )
        else:
            bot1_result = args.bot1_result or dry_bot1_result(task, acceptance, route)
            if route_requires_bot2(route):
                bot2_session_id = f"{pid}-bot2-dry"
                verdict = configured_bot2_verdict(args)
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
            add_assignment(pid, "tester", "verification", "completed", {"evidence_chars": len(evidence)}, store_path=args.process_store)
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
        add_assignment(pid, "bot2", "quality_gate", "completed", {"session_id": bot2_session_id, "verdict": verdict}, store_path=args.process_store)
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
                    {"max_review_cycles": MAX_BOT_REVIEW_CYCLES, "verdict": verdict},
                    store_path=args.process_store,
                )

    update_task(supervisor_task_id, status=final_status, store_path=args.supervisor_store)
    if final_status == "awaiting_human_decision":
        task_state = get_task(supervisor_task_id, store_path=args.supervisor_store)
        safe_task_state = redact_payload(task_state)
        safe_verdict = redact_payload(verdict)
        create_human_escalation(safe_task_state, bot2_session_id, safe_verdict, store_path=args.supervisor_store)
        human_message = escalation_text(safe_task_state, safe_verdict)
        human_notification = build_human_notification_payload(
            process_id=pid,
            supervisor_task_id=supervisor_task_id,
            task=safe_task_state,
            route=route,
            bot2_session_id=bot2_session_id,
            verdict=safe_verdict,
        )
        notification_delivery = dispatch_human_notification(
            human_notification,
            telegram=args.notify_telegram,
            dry_run=args.notification_dry_run,
        )
        add_supervisor_event(
            supervisor_task_id,
            "human_escalation",
            {"message": human_message, "notification": human_notification, "delivery": notification_delivery},
            store_path=args.supervisor_store,
        )
        add_process_event(
            pid,
            "human_notification",
            {"notification": human_notification, "delivery": notification_delivery},
            store_path=args.process_store,
        )
        add_assignment(
            pid,
            "supervisor",
            "human_decision",
            "waiting",
            {"message": human_message, "notification_event": "human_notification", "delivery": notification_delivery},
            store_path=args.process_store,
        )

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
