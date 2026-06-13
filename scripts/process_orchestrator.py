#!/usr/bin/env python3
"""Process-oriented Hermes Supervisor MVP."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
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
    add_event as add_supervisor_event,
    add_role_run,
    create_human_escalation,
    create_task,
    dumps,
    escalation_text,
    get_task,
    link_bot2,
    parse_bot2_verdict,
    supervisor_status_for_verdict,
    update_task,
)
from task_router import classify_task


PROCESS_STORE_PATH = Path(
    os.environ.get(
        "PROCESS_STORE_PATH",
        "/var/lib/docker/volumes/hermes-data/_data/process_orchestrator_store.db",
    )
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    return parse_bot2_verdict(text)


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


def live_bot1_result(task: str, acceptance: str, *, bot1_model: str, max_tokens: int, timeout: int) -> tuple[str, str, str]:
    import dual_bot_lab as lab

    cfg = lab.bothub_config()
    rid = lab.run_id()
    lab.add_run(rid, task, acceptance, bot1_model, "")
    bot1, bot1_raw = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=bot1_model,
        messages=lab.bot1_messages(task, acceptance),
        max_tokens=max_tokens,
        timeout=timeout,
    )
    lab.add_message(rid, "Bot#1", bot1_model, bot1, {"usage": bot1_raw.get("usage", {})})
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
    bot1, bot1_raw = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=bot1_model,
        messages=lab.bot1_messages(task, acceptance),
        max_tokens=max_tokens,
        timeout=timeout,
    )
    lab.add_message(rid, "Bot#1", bot1_model, bot1, {"usage": bot1_raw.get("usage", {})})
    bot2, bot2_raw = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=bot2_model,
        messages=lab.bot2_messages(task, acceptance, bot1),
        max_tokens=max_tokens,
        timeout=timeout,
    )
    lab.add_message(rid, "Bot#2", bot2_model, bot2, {"usage": bot2_raw.get("usage", {})})
    verdict = parse_verdict(bot2)
    if verdict.get("status") == INVALID_BOT2_STATUS:
        bot2_repair, bot2_repair_raw = lab.call_chat(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=bot2_model,
            messages=lab.bot2_repair_messages(task, acceptance, bot1, bot2),
            max_tokens=max_tokens,
            timeout=timeout,
        )
        lab.add_message(rid, "Bot#2-repair", bot2_model, bot2_repair, {"usage": bot2_repair_raw.get("usage", {})})
        repaired_verdict = parse_verdict(bot2_repair)
        if repaired_verdict.get("status") != INVALID_BOT2_STATUS:
            verdict = repaired_verdict
            bot2 = f"{bot2}\n\n## Bot#2 JSON Repair\n\n{bot2_repair}"
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


def run_process(args: argparse.Namespace) -> dict[str, Any]:
    task = args.task.strip()
    acceptance = args.acceptance.strip()
    route = classify_task(task)
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
                verdict = dry_verdict(args.bot2_status)
        evidence = args.evidence or bot1_result
        update_task(supervisor_task_id, bot1_result=bot1_result, evidence=evidence, store_path=args.supervisor_store)
        add_assignment(pid, "bot1", "execution", "completed", {"result_chars": len(bot1_result)}, store_path=args.process_store)
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
    }


def process_details(pid: str, *, store_path: Path | str | None = None) -> dict[str, Any]:
    with connect(store_path) as con:
        run = con.execute("SELECT * FROM process_runs WHERE id=?", (pid,)).fetchone()
        if not run:
            raise SystemExit(f"process run not found: {pid}")
        events = con.execute(
            "SELECT created_at, event_type, payload_json FROM process_events WHERE process_id=? ORDER BY id",
            (pid,),
        ).fetchall()
        assignments = con.execute(
            "SELECT created_at, worker, phase, status, output_json FROM process_assignments WHERE process_id=? ORDER BY id",
            (pid,),
        ).fetchall()
    data = dict(run)
    data["router"] = json.loads(data.pop("router_json"))
    data["events"] = [dict(row) | {"payload": json.loads(row["payload_json"])} for row in events]
    data["assignments"] = [dict(row) | {"output": json.loads(row["output_json"])} for row in assignments]
    for row in data["events"]:
        row.pop("payload_json", None)
    for row in data["assignments"]:
        row.pop("output_json", None)
    return redact_payload(data)


def cmd_route(args: argparse.Namespace) -> None:
    print(json.dumps(classify_task(args.task), ensure_ascii=False, indent=2))


def cmd_run(args: argparse.Namespace) -> None:
    print(json.dumps(run_process(args), ensure_ascii=False, indent=2))


def cmd_show(args: argparse.Namespace) -> None:
    print(json.dumps(process_details(args.process_id, store_path=args.process_store), ensure_ascii=False, indent=2))


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
    run.add_argument("--live-dual", action="store_true")
    run.add_argument("--bot1-model", default="deepseek-v4-flash")
    run.add_argument("--bot2-model", default="gpt-5.3-codex")
    run.add_argument("--timeout", type=int, default=180)
    run.add_argument("--max-tokens", type=int, default=1400)
    run.add_argument("--notify-telegram", action="store_true", help="Send human-gate notification to Telegram via DevLog settings")
    run.add_argument("--notification-dry-run", action="store_true", help="Build and record the notification payload without network delivery")
    run.set_defaults(func=cmd_run)

    show = sub.add_parser("show")
    show.add_argument("process_id")
    show.set_defaults(func=cmd_show)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
