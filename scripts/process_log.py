#!/usr/bin/env python3
"""Structured JSONL process logging for Hermes runtime glue."""

from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from secret_patterns import redact_payload
except ImportError:  # pragma: no cover
    from scripts.secret_patterns import redact_payload


DEFAULT_LOG_PATH = Path("/opt/data/logs/hermes_process_events.jsonl")
DEFAULT_PROCESS_STORE_PATH = Path(
    os.environ.get(
        "PROCESS_STORE_PATH",
        "/var/lib/docker/volumes/hermes-data/_data/process_orchestrator_store.db",
    )
)
DEFAULT_SUPERVISOR_STORE_PATH = Path(
    os.environ.get(
        "SUPERVISOR_STORE_PATH",
        "/var/lib/docker/volumes/hermes-data/_data/supervisor_store.db",
    )
)

ACTIVE_PROCESS_STATUSES = ("running", "awaiting_human_decision", "return_to_bot1", "failed", "blocked")
RETURN_TO_BOT1_ACTION = "return_to_bot1_with_bot2_fixes"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log_path() -> Path:
    return Path(os.environ.get("HERMES_PROCESS_LOG_PATH", str(DEFAULT_LOG_PATH)))


def log_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    process_id: str = "",
    level: str = "info",
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Append one redacted JSONL event and never raise into the caller."""
    target = Path(path) if path is not None else log_path()
    event = {
        "ts": utc_now(),
        "level": str(level or "info"),
        "event_type": str(event_type or "event"),
        "process_id": str(process_id or ""),
        "payload": redact_payload(payload or {}),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return {"ok": True, "path": str(target)}
    except Exception as exc:  # pragma: no cover - log path must never break Hermes
        return {"ok": False, "path": str(target), "error": f"{type(exc).__name__}: {exc}"}


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    tail = minutes % 60
    return f"{hours}h {tail}m" if tail else f"{hours}h"


def _process_orchestrator():
    try:
        import process_orchestrator
    except ImportError:  # pragma: no cover
        from scripts import process_orchestrator

    return process_orchestrator


def _devlog_sender():
    try:
        from devlog import send_telegram_message
    except ImportError:  # pragma: no cover
        from scripts.devlog import send_telegram_message

    return send_telegram_message


def _latest_activity_at(details: dict[str, Any]) -> str:
    candidates = [
        str(details.get("updated_at") or ""),
        str((details.get("summary") or {}).get("last_event_at") or ""),
    ]
    for assignment in details.get("assignments") or []:
        candidates.append(str(assignment.get("created_at") or ""))
    parsed = [(parse_ts(value), value) for value in candidates if value]
    parsed = [(dt, value) for dt, value in parsed if dt is not None]
    if not parsed:
        return ""
    return max(parsed, key=lambda item: item[0] or datetime.min.replace(tzinfo=timezone.utc))[1]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        clean = str(item or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _assignment_label(assignment: dict[str, Any]) -> str:
    worker = str(assignment.get("worker") or "").strip()
    phase = str(assignment.get("phase") or "").strip()
    return f"{worker}:{phase}" if worker and phase else worker or phase


def _done_items(details: dict[str, Any]) -> list[str]:
    return _dedupe(
        [
            _assignment_label(assignment)
            for assignment in details.get("assignments") or []
            if str(assignment.get("status") or "") == "completed"
        ]
    )


def _pending_items(details: dict[str, Any]) -> list[str]:
    return _dedupe(
        [
            _assignment_label(assignment)
            for assignment in details.get("assignments") or []
            if str(assignment.get("status") or "") in {"pending", "waiting", "running"}
        ]
    )


def _not_done_items(details: dict[str, Any], done: list[str], pending: list[str]) -> list[str]:
    status = str(details.get("status") or "")
    route = details.get("router") or {}
    plan = [str(item) for item in route.get("process_plan") or [] if item]
    done_workers = {item.split(":", 1)[0] for item in done}
    pending_workers = {item.split(":", 1)[0] for item in pending}
    not_done = [
        worker
        for worker in plan
        if worker not in {"router", "supervisor"} and worker not in done_workers and worker not in pending_workers
    ]
    if status == "awaiting_human_decision":
        not_done.append("human_decision")
    elif status == "return_to_bot1":
        not_done.append("bot1_revision")
    elif status == "running":
        phase = str(details.get("current_phase") or "")
        if phase and phase not in {"running", "router"}:
            not_done.append(phase)
    elif status in {"failed", "blocked"}:
        not_done.append("investigation")
    return _dedupe(not_done)


def _resume_command(
    process_id: str,
    *,
    process_store: Path | str | None = None,
    supervisor_store: Path | str | None = None,
    mode: str = "auto",
) -> str:
    command = ["python3", "scripts/process_orchestrator.py"]
    if process_store:
        command.extend(["--process-store", str(process_store)])
    if supervisor_store:
        command.extend(["--supervisor-store", str(supervisor_store)])
    command.extend(["continue", process_id, "--mode", mode])
    return " ".join(command)


def _decision_for_worklog(
    details: dict[str, Any],
    *,
    process_store: Path | str | None = None,
    supervisor_store: Path | str | None = None,
) -> dict[str, Any]:
    status = str(details.get("status") or "")
    summary = details.get("summary") or {}
    next_action = summary.get("next_action") or {}
    route = details.get("router") or {}
    policy = route.get("autonomy_policy") or {}
    parsing_autonomy = policy.get("mode") == "parsing_bot1_bot2_only"
    revision_action = next_action.get("action") == RETURN_TO_BOT1_ACTION
    command = _resume_command(
        str(details.get("id") or summary.get("process_id") or ""),
        process_store=process_store,
        supervisor_store=supervisor_store,
    )

    if status == "awaiting_human_decision":
        return {
            "state": "wait_human",
            "reason": "Bot#2/Supervisor ждёт Да/Нет от пользователя; автопродолжение запрещено.",
            "auto_continue_allowed": False,
            "resume_command": "",
        }
    if status == "return_to_bot1" and revision_action:
        return {
            "state": "ready_to_continue",
            "reason": "Следующий безопасный шаг уже записан: вернуть Bot#1 с правками Bot#2.",
            "auto_continue_allowed": bool(parsing_autonomy),
            "resume_command": command,
        }
    if status == "running":
        return {
            "state": "watch_running",
            "reason": "Процесс числится running; не создаём дубль, пока не станет stale/blocked вручную.",
            "auto_continue_allowed": False,
            "resume_command": "",
        }
    if status in {"failed", "blocked"}:
        return {
            "state": "investigate",
            "reason": summary.get("blocked_reason") or "Процесс завершился ошибкой/блокировкой; нужен разбор лога.",
            "auto_continue_allowed": False,
            "resume_command": "",
        }
    return {
        "state": "done",
        "reason": "Процесс в финальном статусе.",
        "auto_continue_allowed": False,
        "resume_command": "",
    }


def build_process_worklog(
    details: dict[str, Any],
    *,
    now: datetime | None = None,
    idle_after_seconds: int = 900,
    process_store: Path | str | None = None,
    supervisor_store: Path | str | None = None,
) -> dict[str, Any]:
    """Build a compact state ledger Hermes can reread before resuming work."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    summary = details.get("summary") or {}
    last_activity_at = _latest_activity_at(details)
    last_activity_dt = parse_ts(last_activity_at)
    idle_seconds = int((current_time - last_activity_dt).total_seconds()) if last_activity_dt else 0
    idle_seconds = max(0, idle_seconds)
    done = _done_items(details)
    pending = _pending_items(details)
    not_done = _not_done_items(details, done, pending)
    status = str(details.get("status") or summary.get("status") or "")
    decision = _decision_for_worklog(details, process_store=process_store, supervisor_store=supervisor_store)
    is_open = status in ACTIVE_PROCESS_STATUSES
    stale = bool(is_open and idle_after_seconds > 0 and idle_seconds >= idle_after_seconds)
    return redact_payload(
        {
            "process_id": details.get("id") or summary.get("process_id", ""),
            "supervisor_task_id": details.get("supervisor_task_id", ""),
            "status": status,
            "current_phase": details.get("current_phase") or summary.get("current_phase", ""),
            "task_preview": str(details.get("task") or "")[:240],
            "last_activity_at": last_activity_at,
            "idle_seconds": idle_seconds,
            "idle_for": format_seconds(idle_seconds),
            "stale": stale,
            "done": done,
            "pending": pending,
            "not_done": not_done,
            "next_action": summary.get("next_action") or {},
            "waiting_on": summary.get("waiting_on", ""),
            "bot2": summary.get("bot2", {}),
            "human_decision": summary.get("human_decision", {}),
            "decision": decision,
        }
    )


def process_worklog(
    process_id: str,
    *,
    process_store: Path | str | None = None,
    supervisor_store: Path | str | None = None,
    now: datetime | None = None,
    idle_after_seconds: int = 900,
) -> dict[str, Any]:
    orchestrator = _process_orchestrator()
    details = orchestrator.process_details(
        process_id,
        store_path=process_store,
        supervisor_store_path=supervisor_store,
    )
    return build_process_worklog(
        details,
        now=now,
        idle_after_seconds=idle_after_seconds,
        process_store=process_store,
        supervisor_store=supervisor_store,
    )


def list_process_ids(
    *,
    process_store: Path | str | None = None,
    statuses: list[str] | tuple[str, ...] = ACTIVE_PROCESS_STATUSES,
    limit: int = 10,
) -> list[str]:
    orchestrator = _process_orchestrator()
    selected_statuses = [str(status) for status in statuses if str(status)]
    with orchestrator.connect(process_store) as con:
        if selected_statuses:
            placeholders = ",".join("?" for _ in selected_statuses)
            rows = con.execute(
                f"""
                SELECT id
                FROM process_runs
                WHERE status IN ({placeholders})
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (*selected_statuses, int(limit)),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT id
                FROM process_runs
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
    return [str(row["id"]) for row in rows]


def list_process_worklogs(
    *,
    process_store: Path | str | None = None,
    supervisor_store: Path | str | None = None,
    statuses: list[str] | tuple[str, ...] = ACTIVE_PROCESS_STATUSES,
    limit: int = 10,
    now: datetime | None = None,
    idle_after_seconds: int = 900,
) -> list[dict[str, Any]]:
    return [
        process_worklog(
            pid,
            process_store=process_store,
            supervisor_store=supervisor_store,
            now=now,
            idle_after_seconds=idle_after_seconds,
        )
        for pid in list_process_ids(process_store=process_store, statuses=statuses, limit=limit)
    ]


def format_worklog_text(worklog: dict[str, Any]) -> str:
    next_action = worklog.get("next_action") or {}
    decision = worklog.get("decision") or {}
    bot2 = worklog.get("bot2") or {}
    lines = [
        "[Hermes Worklog]",
        f"Process: {worklog.get('process_id', '')}",
        f"Status: {worklog.get('status', '')} / phase={worklog.get('current_phase', '')}",
        f"Idle: {worklog.get('idle_for', '0s')} / stale={bool(worklog.get('stale'))}",
        f"Done: {', '.join(worklog.get('done') or []) or '-'}",
        f"Pending: {', '.join(worklog.get('pending') or []) or '-'}",
        f"Not done: {', '.join(worklog.get('not_done') or []) or '-'}",
        f"Next: {next_action.get('action', '') or '-'} -> {next_action.get('target_worker', '') or '-'}",
        f"Decision: {decision.get('state', '')} ({decision.get('reason', '')})",
    ]
    if bot2.get("status"):
        lines.append(f"Bot#2: {bot2.get('status')} / {bot2.get('summary', '')}")
    if decision.get("resume_command"):
        lines.append(f"Resume: {decision.get('resume_command')}")
    return "\n".join(lines)


def wakeback(
    *,
    process_store: Path | str | None = None,
    supervisor_store: Path | str | None = None,
    idle_after_seconds: int = 900,
    limit: int = 5,
    send_telegram: bool = False,
    telegram_sender: Any | None = None,
    auto_continue: bool = False,
    continue_mode: str = "auto",
    timeout: int = 180,
    max_tokens: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Inspect open processes, remind Telegram, and optionally resume parser-only Bot#1 loops."""
    orchestrator = _process_orchestrator()
    worklogs = list_process_worklogs(
        process_store=process_store,
        supervisor_store=supervisor_store,
        limit=limit,
        now=now,
        idle_after_seconds=idle_after_seconds,
    )
    selected = [
        item
        for item in worklogs
        if item.get("stale")
        or (item.get("decision") or {}).get("state") in {"wait_human", "ready_to_continue", "investigate"}
    ]
    sends: list[dict[str, Any]] = []
    if send_telegram and selected:
        sender = telegram_sender or _devlog_sender()
        text = "\n\n".join(format_worklog_text(item) for item in selected)
        sends.append(sender(text))

    continuations: list[dict[str, Any]] = []
    if auto_continue:
        default_tokens = int(getattr(orchestrator, "DEFAULT_PROCESS_MAX_TOKENS", 3000))
        for item in selected:
            decision = item.get("decision") or {}
            if not decision.get("auto_continue_allowed"):
                continue
            args = Namespace(
                process_id=item.get("process_id"),
                process_store=process_store,
                supervisor_store=supervisor_store,
                mode=continue_mode,
                bot1_model="auto",
                bot2_model="auto",
                timeout=int(timeout),
                max_tokens=int(max_tokens or default_tokens),
                notify_telegram=send_telegram,
                notification_dry_run=not send_telegram,
                rlm_store=None,
                rlm_enabled=False,
            )
            result = orchestrator.continue_process(args)
            continuations.append({"process_id": item.get("process_id"), "result": result})

    return redact_payload(
        {
            "ok": True,
            "checked": len(worklogs),
            "selected": len(selected),
            "worklogs": selected,
            "telegram": sends,
            "continuations": continuations,
        }
    )


def _legacy_event_main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_type")
    parser.add_argument("--process-id", default="")
    parser.add_argument("--level", default="info")
    parser.add_argument("--payload-json", default="{}")
    parser.add_argument("--path", default="")
    args = parser.parse_args(argv)
    payload = json.loads(args.payload_json)
    result = log_event(
        args.event_type,
        payload,
        process_id=args.process_id,
        level=args.level,
        path=args.path or None,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or raw[0] not in {"event", "worklog", "wakeback"}:
        return _legacy_event_main(raw)

    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    event = sub.add_parser("event", help="Append one JSONL process log event")
    event.add_argument("event_type")
    event.add_argument("--process-id", default="")
    event.add_argument("--level", default="info")
    event.add_argument("--payload-json", default="{}")
    event.add_argument("--path", default="")

    worklog_cmd = sub.add_parser("worklog", help="Print compact process resume ledger")
    worklog_cmd.add_argument("--process-id", default="")
    worklog_cmd.add_argument("--process-store", default=str(DEFAULT_PROCESS_STORE_PATH))
    worklog_cmd.add_argument("--supervisor-store", default=str(DEFAULT_SUPERVISOR_STORE_PATH))
    worklog_cmd.add_argument("--statuses", nargs="*", default=list(ACTIVE_PROCESS_STATUSES))
    worklog_cmd.add_argument("--limit", type=int, default=10)
    worklog_cmd.add_argument("--idle-minutes", type=int, default=15)
    worklog_cmd.add_argument("--format", choices=["json", "text"], default="json")

    wakeback_cmd = sub.add_parser("wakeback", help="Inspect open processes and optionally notify/continue safe parser loops")
    wakeback_cmd.add_argument("--process-store", default=str(DEFAULT_PROCESS_STORE_PATH))
    wakeback_cmd.add_argument("--supervisor-store", default=str(DEFAULT_SUPERVISOR_STORE_PATH))
    wakeback_cmd.add_argument("--idle-minutes", type=int, default=15)
    wakeback_cmd.add_argument("--limit", type=int, default=5)
    wakeback_cmd.add_argument("--telegram", action="store_true")
    wakeback_cmd.add_argument("--auto-continue", action="store_true")
    wakeback_cmd.add_argument("--continue-mode", choices=["auto", "dry", "live"], default="auto")
    wakeback_cmd.add_argument("--timeout", type=int, default=180)
    wakeback_cmd.add_argument("--max-tokens", type=int, default=0)

    args = parser.parse_args(raw)
    if args.cmd == "event":
        payload = json.loads(args.payload_json)
        result = log_event(
            args.event_type,
            payload,
            process_id=args.process_id,
            level=args.level,
            path=args.path or None,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("ok") else 1

    idle_seconds = int(args.idle_minutes) * 60
    if args.cmd == "worklog":
        if args.process_id:
            result: Any = process_worklog(
                args.process_id,
                process_store=args.process_store,
                supervisor_store=args.supervisor_store,
                idle_after_seconds=idle_seconds,
            )
            if args.format == "text":
                print(format_worklog_text(result))
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        result = list_process_worklogs(
            process_store=args.process_store,
            supervisor_store=args.supervisor_store,
            statuses=args.statuses,
            limit=args.limit,
            idle_after_seconds=idle_seconds,
        )
        if args.format == "text":
            print("\n\n".join(format_worklog_text(item) for item in result))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    result = wakeback(
        process_store=args.process_store,
        supervisor_store=args.supervisor_store,
        idle_after_seconds=idle_seconds,
        limit=args.limit,
        send_telegram=bool(args.telegram),
        auto_continue=bool(args.auto_continue),
        continue_mode=args.continue_mode,
        timeout=args.timeout,
        max_tokens=args.max_tokens or None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
