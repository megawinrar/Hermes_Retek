#!/usr/bin/env python3
"""Shared primitives for Hermes Supervisor MVP.

This module is intentionally framework-free: SQLite, subprocess, and small
helpers only. It is designed to run on the server under /opt/hermes-assistant
and in tests from a temporary store.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from sqlite_utils import connect as sqlite_connect
except ImportError:  # pragma: no cover - package-style import fallback
    from scripts.sqlite_utils import connect as sqlite_connect


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT_DIR = Path(os.environ.get("HERMES_PROJECT_DIR", "/opt/hermes-assistant"))
DEFAULT_STORE_PATH = Path(
    os.environ.get(
        "SUPERVISOR_STORE_PATH",
        "/var/lib/docker/volumes/hermes-data/_data/supervisor_store.db",
    )
)
DEFAULT_BOT2_GATE = Path(os.environ.get("BOT2_GATE_PATH", "/opt/hermes-assistant/scripts/bot2_gate.py"))
MAX_BOT_REVIEW_CYCLES = int(os.environ.get("HERMES_MAX_BOT_REVIEW_CYCLES", "3"))
_INITIALIZED_SUPERVISOR_STORES: set[str] = set()

APPROVED_STATUSES = {"APPROVE", "APPROVE_WITH_EVIDENCE"}
ESCALATION_STATUSES = {
    "REJECT",
    "NEEDS_HUMAN",
    "NEED_HUMAN_DECISION",
    "REQUEST_CHANGES",
    "INSUFFICIENT_EVIDENCE",
    "RUBBER_STAMP_RISK",
    "FAKE_IMPLEMENTATION_DETECTED",
    "MISSING_TESTS_FOR_CODE_CHANGE",
    "TEST_THEATER_DETECTED",
    "REFACTORING_REQUIRED",
}
BLOCKED_STATUSES = {"BLOCKED_BY_POLICY", "LOOP_DETECTED"}
INVALID_BOT2_STATUS = "INVALID_BOT2_OUTPUT"
BOT2_VERDICT_STATUSES = APPROVED_STATUSES | ESCALATION_STATUSES | BLOCKED_STATUSES | {INVALID_BOT2_STATUS}
BOT2_SUMMARY_MAX_CHARS = 180
BOT2_EVIDENCE_MAX_ITEMS = 3
BOT2_RISK_MAX_ITEMS = 3
BOT2_FIX_MAX_ITEMS = 3
BOT2_EVIDENCE_ITEM_MAX_CHARS = 120
BOT2_RISK_ITEM_MAX_CHARS = 160
BOT2_FIX_ITEM_MAX_CHARS = 180

YES_MEANING = "Согласен с Bot#2: вернуть Bot#1 на доработку."
NO_MEANING = "Отклонить возражение Bot#2: принять работу Bot#1 как есть."

SUPERVISOR_STATUSES = {
    "created",
    "running",
    "approved",
    "approved_refusal",
    "awaiting_human_decision",
    "return_to_bot1",
    "accepted_by_user_override",
    "failed",
    "blocked",
}
ALLOWED_STATUS_TRANSITIONS = {
    "created": {"running", "failed", "blocked"},
    "running": {"approved", "approved_refusal", "awaiting_human_decision", "failed", "blocked"},
    "awaiting_human_decision": {"return_to_bot1", "accepted_by_user_override", "failed", "blocked"},
    "return_to_bot1": {"running", "failed", "blocked"},
    "approved": set(),
    "approved_refusal": set(),
    "accepted_by_user_override": set(),
    "failed": set(),
    "blocked": set(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def loads(raw: str | None, default: Any = None) -> Any:
    if not raw:
        return default
    return json.loads(raw)


def task_id() -> str:
    return f"sup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def connect(store_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(store_path or DEFAULT_STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    store_key = str(path)
    existed_before = path.exists()
    con = sqlite_connect(path)
    con.row_factory = sqlite3.Row
    if not existed_before or store_key not in _INITIALIZED_SUPERVISOR_STORES:
        con.execute("PRAGMA journal_mode=WAL")
        init_schema(con)
        _INITIALIZED_SUPERVISOR_STORES.add(store_key)
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS supervisor_tasks (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            tz TEXT NOT NULL,
            acceptance_contract_json TEXT NOT NULL,
            risk_level TEXT NOT NULL,
            status TEXT NOT NULL,
            bot1_result TEXT DEFAULT '',
            evidence TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS supervisor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES supervisor_tasks(id)
        );

        CREATE TABLE IF NOT EXISTS supervisor_role_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT DEFAULT '',
            evidence_json TEXT DEFAULT '{}',
            FOREIGN KEY(task_id) REFERENCES supervisor_tasks(id)
        );

        CREATE TABLE IF NOT EXISTS supervisor_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            artifact_type TEXT NOT NULL,
            path TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY(task_id) REFERENCES supervisor_tasks(id)
        );

        CREATE TABLE IF NOT EXISTS supervisor_bot2_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            bot2_session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            verdict_status TEXT NOT NULL,
            verdict_json TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES supervisor_tasks(id)
        );

        CREATE TABLE IF NOT EXISTS supervisor_human_escalations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            bot2_session_id TEXT,
            created_at TEXT NOT NULL,
            decision_at TEXT,
            choice TEXT,
            meaning TEXT,
            reason TEXT DEFAULT '',
            bot1_version TEXT NOT NULL,
            bot2_version TEXT NOT NULL,
            risk TEXT DEFAULT '',
            recommendation TEXT DEFAULT '',
            status TEXT NOT NULL,
            FOREIGN KEY(task_id) REFERENCES supervisor_tasks(id)
        );

        CREATE TABLE IF NOT EXISTS supervisor_resource_locks (
            resource TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            reason TEXT NOT NULL,
            command TEXT DEFAULT '',
            FOREIGN KEY(task_id) REFERENCES supervisor_tasks(id)
        );
        """
    )
    con.commit()


def build_acceptance_contract(tz: str) -> dict[str, Any]:
    lower = tz.lower()
    risk_level = "medium"
    if any(word in lower for word in ["prod", "production", "deploy", "server", "token", "secret", "database", "db"]):
        risk_level = "high"
    if len(tz.strip()) < 40:
        risk_level = "low"

    required_tests = [
        "python3 -m py_compile changed Python scripts when applicable",
        "run focused unit/smoke tests for touched code",
        "capture command, exit code, and short output summary",
    ]
    required_evidence = [
        "Bot#1 result summary",
        "changed files or explicit no-file-change statement",
        "test commands with exit codes",
        "risks and rollback notes when server/runtime is involved",
    ]
    return {
        "tz": tz,
        "risk_level": risk_level,
        "acceptance_criteria": [
            "The result directly satisfies the user TZ.",
            "Evidence is concrete enough for Bot#2 to verify.",
            "No deploy or push occurs before Bot#2 approval or user override.",
            "Unresolved Bot#1/Bot#2 disagreement is escalated with Да/Нет choice.",
        ],
        "required_tests": required_tests,
        "required_evidence": required_evidence,
        "human_decision_semantics": {
            "yes": YES_MEANING,
            "no": NO_MEANING,
        },
    }


def create_task(tz: str, *, store_path: Path | str | None = None) -> dict[str, Any]:
    contract = build_acceptance_contract(tz)
    tid = task_id()
    now = utc_now()
    with connect(store_path) as con:
        con.execute(
            """
            INSERT INTO supervisor_tasks
              (id, created_at, updated_at, tz, acceptance_contract_json, risk_level, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (tid, now, now, tz, dumps(contract), contract["risk_level"], "created"),
        )
        con.execute(
            """
            INSERT INTO supervisor_events(task_id, created_at, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (tid, now, "task_created", dumps({"risk_level": contract["risk_level"]})),
        )
        con.commit()
    return {"task_id": tid, "status": "created", "acceptance_contract": contract}


def get_task(task_id_value: str, *, store_path: Path | str | None = None) -> dict[str, Any]:
    with connect(store_path) as con:
        row = con.execute("SELECT * FROM supervisor_tasks WHERE id=?", (task_id_value,)).fetchone()
    if not row:
        raise SystemExit(f"task not found: {task_id_value}")
    data = dict(row)
    data["acceptance_contract"] = loads(data.pop("acceptance_contract_json"), {})
    return data


def list_tasks(limit: int = 20, *, store_path: Path | str | None = None) -> list[dict[str, Any]]:
    with connect(store_path) as con:
        rows = con.execute(
            """
            SELECT id, created_at, updated_at, risk_level, status, substr(tz, 1, 120) AS tz
            FROM supervisor_tasks
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_event(task_id_value: str, event_type: str, payload: dict[str, Any], *, store_path: Path | str | None = None) -> None:
    with connect(store_path) as con:
        con.execute(
            "INSERT INTO supervisor_events(task_id, created_at, event_type, payload_json) VALUES (?, ?, ?, ?)",
            (task_id_value, utc_now(), event_type, dumps(payload)),
        )
        con.commit()


def add_role_run(
    task_id_value: str,
    role: str,
    status: str,
    summary: str,
    evidence: dict[str, Any] | None = None,
    *,
    store_path: Path | str | None = None,
) -> None:
    with connect(store_path) as con:
        con.execute(
            """
            INSERT INTO supervisor_role_runs(task_id, created_at, role, status, summary, evidence_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (task_id_value, utc_now(), role, status, summary, dumps(evidence or {})),
        )
        con.commit()


def validate_status_transition(current_status: str, next_status: str) -> None:
    current = str(current_status or "").strip()
    next_value = str(next_status or "").strip()
    if current == next_value:
        return
    if current not in SUPERVISOR_STATUSES:
        raise SystemExit(f"unknown current supervisor status: {current}")
    if next_value not in SUPERVISOR_STATUSES:
        raise SystemExit(f"unknown next supervisor status: {next_value}")
    if next_value not in ALLOWED_STATUS_TRANSITIONS[current]:
        raise SystemExit(f"illegal supervisor transition: {current} -> {next_value}")


def bot2_cycle_count(task_id_value: str, *, store_path: Path | str | None = None) -> int:
    with connect(store_path) as con:
        row = con.execute("SELECT COUNT(*) AS count FROM supervisor_bot2_links WHERE task_id=?", (task_id_value,)).fetchone()
    return int(row["count"] if row else 0)


def enforce_bot_loop_guard(
    task_id_value: str,
    *,
    current_status: str,
    next_status: str,
    store_path: Path | str | None = None,
) -> None:
    if current_status != "return_to_bot1" or next_status != "running":
        return
    cycles = bot2_cycle_count(task_id_value, store_path=store_path)
    if cycles < MAX_BOT_REVIEW_CYCLES:
        return
    with connect(store_path) as con:
        con.execute(
            "UPDATE supervisor_tasks SET status=?, updated_at=? WHERE id=?",
            ("blocked", utc_now(), task_id_value),
        )
        con.execute(
            "INSERT INTO supervisor_events(task_id, created_at, event_type, payload_json) VALUES (?, ?, ?, ?)",
            (
                task_id_value,
                utc_now(),
                "bot_loop_guard",
                dumps({"cycles": cycles, "max_cycles": MAX_BOT_REVIEW_CYCLES, "blocked_status": "blocked"}),
            ),
        )
        con.commit()
    raise SystemExit(f"bot loop guard blocked restart after {cycles} Bot#2 cycles")


def update_task(
    task_id_value: str,
    *,
    status: str | None = None,
    bot1_result: str | None = None,
    evidence: str | None = None,
    store_path: Path | str | None = None,
) -> None:
    fields: dict[str, str] = {"updated_at": utc_now()}
    if status is not None:
        fields["status"] = status
    if bot1_result is not None:
        fields["bot1_result"] = bot1_result
    if evidence is not None:
        fields["evidence"] = evidence
    assignments = ", ".join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [task_id_value]
    with connect(store_path) as con:
        if status is not None:
            row = con.execute("SELECT status FROM supervisor_tasks WHERE id=?", (task_id_value,)).fetchone()
            if not row:
                raise SystemExit(f"task not found: {task_id_value}")
            current_status = str(row["status"])
            validate_status_transition(current_status, status)
            enforce_bot_loop_guard(
                task_id_value,
                current_status=current_status,
                next_status=status,
                store_path=store_path,
            )
        con.execute(f"UPDATE supervisor_tasks SET {assignments} WHERE id=?", values)
        con.commit()


def link_bot2(
    task_id_value: str,
    bot2_session_id: str,
    verdict: dict[str, Any],
    *,
    store_path: Path | str | None = None,
) -> None:
    status = normalize_verdict_status(verdict)
    with connect(store_path) as con:
        con.execute(
            """
            INSERT INTO supervisor_bot2_links
              (task_id, bot2_session_id, created_at, verdict_status, verdict_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task_id_value, bot2_session_id, utc_now(), status, dumps(verdict)),
        )
        con.commit()


def normalize_verdict_status(verdict: dict[str, Any]) -> str:
    return str(verdict.get("status") or "UNKNOWN").upper()


def supervisor_status_for_verdict(verdict: dict[str, Any]) -> str:
    status = normalize_verdict_status(verdict)
    if status in APPROVED_STATUSES:
        if str(verdict.get("approved_action") or "execute").lower() == "refuse":
            return "approved_refusal"
        return "approved"
    if status in ESCALATION_STATUSES:
        return "awaiting_human_decision"
    if status in BLOCKED_STATUSES:
        return "blocked"
    return "failed"


def invalid_bot2_verdict(reason: str, raw: str = "") -> dict[str, Any]:
    return {
        "status": INVALID_BOT2_STATUS,
        "summary": "Bot#2 output failed the machine-readable verdict contract.",
        "risks": [reason],
        "required_fixes": ["Retry Bot#2 with the strict JSON contract or inspect the transcript."],
        "confidence": 0.0,
        "raw_chars": len(raw or ""),
    }


def _strip_single_json_fence(raw: str) -> str:
    stripped = raw.strip()
    match = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.S)
    return match.group(1).strip() if match else stripped


def _compact_text(value: Any, max_chars: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 15)].rstrip() + "...[truncated]"


def _compact_text_list(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    elif value in (None, ""):
        raw_items = []
    else:
        raw_items = [value]
    items: list[str] = []
    for raw_item in raw_items:
        item = _compact_text(raw_item, max_chars)
        if item:
            items.append(item)
        if len(items) >= max_items:
            break
    return items


def compact_bot2_verdict(verdict: dict[str, Any]) -> dict[str, Any]:
    """Keep Bot#2 as a compact defect-review package for Bot#1 and Telegram."""
    compact = dict(verdict)
    compact["summary"] = _compact_text(compact.get("summary", ""), BOT2_SUMMARY_MAX_CHARS)
    compact["evidence_checked"] = _compact_text_list(
        compact.get("evidence_checked"),
        max_items=BOT2_EVIDENCE_MAX_ITEMS,
        max_chars=BOT2_EVIDENCE_ITEM_MAX_CHARS,
    )
    compact["risks"] = _compact_text_list(
        compact.get("risks"),
        max_items=BOT2_RISK_MAX_ITEMS,
        max_chars=BOT2_RISK_ITEM_MAX_CHARS,
    )
    compact["required_fixes"] = _compact_text_list(
        compact.get("required_fixes"),
        max_items=BOT2_FIX_MAX_ITEMS,
        max_chars=BOT2_FIX_ITEM_MAX_CHARS,
    )
    return compact


def parse_bot2_verdict(raw: str) -> dict[str, Any]:
    payload = _strip_single_json_fence(raw)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return invalid_bot2_verdict("invalid_json", raw)
    if not isinstance(data, dict):
        return invalid_bot2_verdict("json_not_object", raw)
    status = normalize_verdict_status(data)
    if status not in BOT2_VERDICT_STATUSES or status == INVALID_BOT2_STATUS:
        return invalid_bot2_verdict(f"unknown_status:{status}", raw)
    data["status"] = status
    if status in APPROVED_STATUSES:
        data.setdefault("approved_action", "execute")
    return compact_bot2_verdict(data)


def extract_bot2_verdict(raw: str) -> dict[str, Any]:
    direct = parse_bot2_verdict(raw)
    if direct.get("status") != INVALID_BOT2_STATUS:
        return direct
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", raw, flags=re.S)
    brace_candidates = re.findall(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", raw, flags=re.S)
    for candidate in fenced + brace_candidates:
        parsed = parse_bot2_verdict(candidate)
        if parsed.get("status") != INVALID_BOT2_STATUS:
            return parsed
    return direct


def escalation_text(task: dict[str, Any], verdict: dict[str, Any]) -> str:
    bot1_version = (task.get("bot1_result") or "").strip() or "Bot#1 result is empty."
    summary = str(verdict.get("summary") or "Bot#2 did not provide a summary.")
    risks = verdict.get("risks") or []
    fixes = verdict.get("required_fixes") or []
    risk = "; ".join(str(item) for item in risks) if risks else "No explicit risk listed."
    recommendation = "; ".join(str(item) for item in fixes) if fixes else "Ask user before continuing."
    return (
        "Сообщение от Bot#2\n\n"
        f"Версия Bot#1:\n{bot1_version}\n\n"
        f"Версия Bot#2:\n{summary}\n\n"
        f"Риск:\n{risk}\n\n"
        f"Рекомендация Bot#2:\n{recommendation}\n\n"
        "Да — согласен с Bot#2, вернуть Bot#1 на доработку.\n"
        "Нет — отклонить возражение Bot#2 и принять работу Bot#1 как есть."
    )


def create_human_escalation(
    task: dict[str, Any],
    bot2_session_id: str,
    verdict: dict[str, Any],
    *,
    store_path: Path | str | None = None,
) -> None:
    risks = verdict.get("risks") or []
    fixes = verdict.get("required_fixes") or []
    with connect(store_path) as con:
        con.execute(
            """
            INSERT INTO supervisor_human_escalations
              (task_id, bot2_session_id, created_at, bot1_version, bot2_version, risk, recommendation, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["id"],
                bot2_session_id,
                utc_now(),
                task.get("bot1_result") or "",
                str(verdict.get("summary") or ""),
                "; ".join(str(item) for item in risks),
                "; ".join(str(item) for item in fixes),
                "awaiting_decision",
            ),
        )
        con.commit()


def record_human_decision(
    task_id_value: str,
    choice: str,
    reason: str,
    *,
    store_path: Path | str | None = None,
) -> dict[str, str | None]:
    normalized = choice.lower().strip()
    if normalized not in {"yes", "no"}:
        raise SystemExit("--choice must be yes or no")
    meaning = YES_MEANING if normalized == "yes" else NO_MEANING
    status = "return_to_bot1" if normalized == "yes" else "accepted_by_user_override"
    with connect(store_path) as con:
        pending = con.execute(
            """
            SELECT id, bot2_session_id
            FROM supervisor_human_escalations
            WHERE task_id=? AND status='awaiting_decision'
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id_value,),
        ).fetchone()
        if not pending:
            raise SystemExit(f"no pending human escalation for task: {task_id_value}")

        con.execute(
            """
            UPDATE supervisor_human_escalations
            SET decision_at=?, choice=?, meaning=?, reason=?, status=?
            WHERE id=?
            """,
            (utc_now(), normalized, meaning, reason or "", "decided", pending["id"]),
        )
        con.execute(
            "UPDATE supervisor_tasks SET status=?, updated_at=? WHERE id=?",
            (status, utc_now(), task_id_value),
        )
        con.execute(
            "INSERT INTO supervisor_events(task_id, created_at, event_type, payload_json) VALUES (?, ?, ?, ?)",
            (task_id_value, utc_now(), "human_decision", dumps({"choice": normalized, "meaning": meaning, "reason": reason or ""})),
        )
        con.commit()
    return {
        "task_id": task_id_value,
        "choice": normalized,
        "meaning": meaning,
        "status": status,
        "bot2_session_id": str(pending["bot2_session_id"] or "") or None,
    }


def active_resource_lock(resource: str, *, store_path: Path | str | None = None) -> dict[str, Any] | None:
    with connect(store_path) as con:
        row = con.execute(
            "SELECT resource, task_id, acquired_at, reason, command FROM supervisor_resource_locks WHERE resource=?",
            (resource,),
        ).fetchone()
    return dict(row) if row else None


def acquire_resource_locks(
    task_id_value: str,
    resources: list[str],
    *,
    reason: str,
    command: str = "",
    store_path: Path | str | None = None,
) -> list[str]:
    if not resources:
        return []
    unique = sorted(set(resources))
    acquired: list[str] = []
    with connect(store_path) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            for resource in unique:
                existing = con.execute(
                    "SELECT task_id FROM supervisor_resource_locks WHERE resource=?",
                    (resource,),
                ).fetchone()
                if existing:
                    raise SystemExit(f"resource locked: {resource} by {existing['task_id']}")
            for resource in unique:
                con.execute(
                    """
                    INSERT INTO supervisor_resource_locks(resource, task_id, acquired_at, reason, command)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (resource, task_id_value, utc_now(), reason, command),
                )
                acquired.append(resource)
            con.commit()
        except BaseException:
            con.rollback()
            raise
    add_event(
        task_id_value,
        "resource_locks_acquired",
        {"resources": acquired, "reason": reason, "command": command},
        store_path=store_path,
    )
    return acquired


def release_resource_locks(
    task_id_value: str,
    resources: list[str],
    *,
    store_path: Path | str | None = None,
) -> None:
    unique = sorted(set(resources))
    if not unique:
        return
    with connect(store_path) as con:
        con.executemany(
            "DELETE FROM supervisor_resource_locks WHERE task_id=? AND resource=?",
            [(task_id_value, resource) for resource in unique],
        )
        con.commit()
    add_event(task_id_value, "resource_locks_released", {"resources": unique}, store_path=store_path)


def run_subprocess(cmd: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)


def python_script_cmd(path: Path) -> list[str]:
    if path.suffix == ".py":
        return [sys.executable, str(path)]
    return [str(path)]


def extract_json_object(raw: str) -> dict[str, Any]:
    stripped = _strip_single_json_fence(raw)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError("subprocess output must be exactly one JSON object") from exc
    if not isinstance(data, dict):
        raise ValueError("subprocess output must be a JSON object")
    return data


def call_bot2_gate(
    *,
    bot2_gate: Path,
    task: str,
    acceptance: str,
    bot1_result: str,
    evidence: str,
    no_telegram: bool = True,
    timeout: int = 900,
) -> tuple[str, dict[str, Any], str]:
    cmd = python_script_cmd(bot2_gate) + [
        "review",
        "--task",
        task,
        "--acceptance",
        acceptance,
        "--bot1-result",
        bot1_result,
        "--evidence",
        evidence,
    ]
    if no_telegram:
        cmd.append("--no-telegram")
    result = run_subprocess(cmd, timeout=timeout)
    raw = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError(f"bot2_gate failed with exit {result.returncode}: {raw.strip()}")
    data = extract_json_object(raw)
    session_id = str(data.get("session_id") or "")
    verdict = data.get("verdict") or {}
    if not session_id or not isinstance(verdict, dict):
        raise ValueError(f"bot2_gate output missing session_id/verdict: {raw.strip()}")
    verdict = parse_bot2_verdict(json.dumps(verdict, ensure_ascii=False))
    return session_id, verdict, raw


def call_bot2_decide(
    *,
    bot2_gate: Path,
    session_id: str,
    choice: str,
    reason: str,
    timeout: int = 120,
) -> None:
    cmd = python_script_cmd(bot2_gate) + ["decide", session_id, "--choice", choice, "--reason", reason or ""]
    result = run_subprocess(cmd, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(f"bot2_gate decide failed with exit {result.returncode}: {(result.stdout or '') + (result.stderr or '')}")


def task_details(task_id_value: str, *, store_path: Path | str | None = None) -> dict[str, Any]:
    task = get_task(task_id_value, store_path=store_path)
    with connect(store_path) as con:
        events = con.execute(
            "SELECT created_at, event_type, payload_json FROM supervisor_events WHERE task_id=? ORDER BY id",
            (task_id_value,),
        ).fetchall()
        runs = con.execute(
            "SELECT created_at, role, status, summary, evidence_json FROM supervisor_role_runs WHERE task_id=? ORDER BY id",
            (task_id_value,),
        ).fetchall()
        links = con.execute(
            "SELECT created_at, bot2_session_id, verdict_status, verdict_json FROM supervisor_bot2_links WHERE task_id=? ORDER BY id",
            (task_id_value,),
        ).fetchall()
        escalations = con.execute(
            """
            SELECT created_at, decision_at, choice, meaning, reason, status, bot2_session_id, bot2_version, risk, recommendation
            FROM supervisor_human_escalations
            WHERE task_id=?
            ORDER BY id
            """,
            (task_id_value,),
        ).fetchall()
    task["events"] = [dict(row) | {"payload": loads(row["payload_json"], {})} for row in events]
    for event in task["events"]:
        event.pop("payload_json", None)
    task["role_runs"] = [dict(row) | {"evidence": loads(row["evidence_json"], {})} for row in runs]
    for run in task["role_runs"]:
        run.pop("evidence_json", None)
    task["bot2_links"] = [dict(row) | {"verdict": loads(row["verdict_json"], {})} for row in links]
    for link in task["bot2_links"]:
        link.pop("verdict_json", None)
    task["human_escalations"] = [dict(row) for row in escalations]
    return task
