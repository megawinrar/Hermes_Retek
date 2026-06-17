#!/usr/bin/env python3
"""Host-side Bot#2 review gate for Hermes Retek.

This script intentionally lives outside hermes-core. It creates an auditable
review session, runs Hermes as Bot#2 through the live container, stores a
machine-readable verdict, and escalates unresolved disagreement to a human.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from _common import gen_id, read_env_file, utc_now
from dual_bot_lab import BOT2_VERDICT_JSON_SCHEMA, bot2_repair_messages
from human_notification import redact_payload, redact_text
from supervisor_common import (
    ESCALATION_STATUSES,
    HUMAN_DECISION_NO_STATUS,
    HUMAN_DECISION_YES_STATUS,
    INVALID_BOT2_STATUS,
    REPAIR_STATUS_FAILED_CLOSED,
    REPAIR_STATUS_REPAIRED,
    parse_bot2_verdict,
)


PROJECT_DIR = Path(os.environ.get("HERMES_PROJECT_DIR", "/opt/hermes-assistant"))
STORE_PATH = Path(
    os.environ.get(
        "BOT2_REVIEW_STORE",
        "/var/lib/docker/volumes/hermes-data/_data/bot2_review_store.db",
    )
)
HERMES_BIN = os.environ.get("HERMES_BIN", "/opt/hermes/bin/hermes")
HERMES_CONTAINER = os.environ.get("HERMES_CONTAINER", "hermes-agent")
DEFAULT_TELEGRAM_CHAT_ID = "245167740"


def session_id() -> str:
    return gen_id("bot2")


def load_env(path: Path | None = None) -> dict[str, str]:
    return read_env_file(path or PROJECT_DIR / ".env")


ENV = load_env()


def dumps(data: Any) -> str:
    return json.dumps(redact_payload(data), ensure_ascii=False, sort_keys=True)


def db(store_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(store_path or STORE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS bot2_review_sessions (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            mode TEXT NOT NULL,
            task TEXT NOT NULL,
            acceptance_criteria TEXT NOT NULL,
            status TEXT NOT NULL,
            bot1_result TEXT DEFAULT '',
            evidence TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS bot2_review_rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            round_no INTEGER NOT NULL,
            speaker TEXT NOT NULL,
            message TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES bot2_review_sessions(id)
        );

        CREATE TABLE IF NOT EXISTS bot2_verdicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            verdict_json TEXT NOT NULL,
            raw_output TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES bot2_review_sessions(id)
        );

        CREATE TABLE IF NOT EXISTS bot2_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES bot2_review_sessions(id)
        );

        CREATE TABLE IF NOT EXISTS human_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            choice TEXT NOT NULL,
            meaning TEXT NOT NULL,
            reason TEXT DEFAULT '',
            FOREIGN KEY(session_id) REFERENCES bot2_review_sessions(id)
        );
        """
    )
    con.commit()
    return con


def add_event(session_id_value: str, event_type: str, payload: dict[str, Any], *, store_path: Path | str | None = None) -> None:
    with db(store_path) as con:
        con.execute(
            "INSERT INTO bot2_events(session_id, created_at, event_type, payload_json) VALUES (?, ?, ?, ?)",
            (session_id_value, utc_now(), event_type, dumps(payload)),
        )
        con.commit()


def add_round(
    session_id_value: str,
    round_no: int,
    speaker: str,
    message: str,
    *,
    store_path: Path | str | None = None,
) -> None:
    with db(store_path) as con:
        con.execute(
            "INSERT INTO bot2_review_rounds(session_id, created_at, round_no, speaker, message) VALUES (?, ?, ?, ?, ?)",
            (session_id_value, utc_now(), round_no, speaker, redact_text(message)),
        )
        con.commit()


def update_session(session_id_value: str, *, store_path: Path | str | None = None, **fields: str) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now()
    redacted = {key: redact_text(value) for key, value in fields.items()}
    assignments = ", ".join(f"{key}=?" for key in redacted)
    values = list(redacted.values()) + [session_id_value]
    with db(store_path) as con:
        con.execute(f"UPDATE bot2_review_sessions SET {assignments} WHERE id=?", values)
        con.commit()


def create_session(mode: str, task: str, acceptance: str, *, store_path: Path | str | None = None) -> str:
    sid = session_id()
    now = utc_now()
    with db(store_path) as con:
        con.execute(
            """
            INSERT INTO bot2_review_sessions
              (id, created_at, updated_at, mode, task, acceptance_criteria, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, now, now, mode, redact_text(task), redact_text(acceptance), "created"),
        )
        con.commit()
    return sid


def telegram_chat_id() -> str:
    explicit = ENV.get("BOT2_DEVLOG_CHAT_ID") or ENV.get("TELEGRAM_CHAT_ID")
    if explicit:
        return explicit
    users = ENV.get("TELEGRAM_ALLOWED_USERS") or ""
    first = users.split(",")[0].strip()
    return first or DEFAULT_TELEGRAM_CHAT_ID


def send_telegram(text: str, *, silent: bool = False) -> bool:
    token = ENV.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [text[i : i + 3500] for i in range(0, len(text), 3500)] or [text]
    delivered = True
    for chunk in chunks:
        payload = {
            "chat_id": telegram_chat_id(),
            "text": redact_text(chunk),
            "disable_notification": silent,
        }
        cmd = [
            "curl",
            "-sS",
            "--max-time",
            "20",
            "--socks5",
            "127.0.0.1:1080",
            "-X",
            "POST",
            url,
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload, ensure_ascii=False),
        ]
        result = subprocess.run(cmd, text=True, capture_output=True, check=False)
        delivered = delivered and result.returncode == 0
    return delivered


def devlog(
    session_id_value: str,
    title: str,
    body: str,
    *,
    notify: bool = True,
    silent: bool = False,
    store_path: Path | str | None = None,
) -> None:
    add_event(session_id_value, "devlog", {"title": title, "body": body}, store_path=store_path)
    if notify:
        send_telegram(f"[Hermes Bot#2 DevLog]\n{title}\nSession: {session_id_value}\n\n{body}", silent=silent)


def run_hermes(prompt: str, *, toolsets: str = "", timeout: int = 600) -> tuple[int, str]:
    cmd = ["docker", "exec", HERMES_CONTAINER, HERMES_BIN, "-z", prompt]
    if toolsets:
        cmd.extend(["-t", toolsets])
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    return result.returncode, redact_text((result.stdout + result.stderr).strip())


def bot2_prompt(task: str, acceptance: str, bot1_result: str, evidence: str) -> str:
    return f"""
You are Bot#2, an independent Hermes reviewer and quality gate.

You are not a free-chat participant. You review the result submitted by Bot#1.
Be skeptical, evidence-based, and concise. Do not approve by style or agreement.

Task:
{task}

Acceptance criteria:
{acceptance}

Bot#1 result:
{bot1_result}

Evidence supplied:
{evidence}

Return ONLY valid JSON matching this schema:
{BOT2_VERDICT_JSON_SCHEMA}

Rules:
- APPROVE only if acceptance criteria are satisfied by evidence.
- REQUEST_CHANGES or REJECT if evidence is missing, fake implementation is likely, tests are weak, or a defect is clear.
- NEEDS_HUMAN if disagreement changes scope, business logic, cost, security, or production behavior.
""".strip()


def repair_prompt(task: str, acceptance: str, bot1_result: str, invalid_output: str) -> str:
    messages = bot2_repair_messages(task, acceptance, bot1_result, invalid_output)
    return "\n\n".join(f"{message['role'].upper()}:\n{message['content']}" for message in messages)


def parse_verdict(raw: str) -> dict[str, Any]:
    return parse_bot2_verdict(raw)


def store_verdict(
    session_id_value: str,
    verdict: dict[str, Any],
    raw: str,
    *,
    store_path: Path | str | None = None,
) -> None:
    with db(store_path) as con:
        con.execute(
            "INSERT INTO bot2_verdicts(session_id, created_at, verdict_json, raw_output) VALUES (?, ?, ?, ?)",
            (session_id_value, utc_now(), dumps(verdict), redact_text(raw)),
        )
        con.commit()


def verdict_text(verdict: dict[str, Any]) -> str:
    lines = [
        f"Status: {verdict.get('status', 'UNKNOWN')}",
        f"Summary: {verdict.get('summary', '')}",
    ]
    for key, label in [
        ("evidence_checked", "Evidence checked"),
        ("risks", "Risks"),
        ("required_fixes", "Required fixes"),
    ]:
        values = verdict.get(key) or []
        if values:
            lines.append(f"{label}:")
            lines.extend(f"- {value}" for value in values)
    if verdict.get("repair_attempted"):
        lines.append(f"Repair status: {verdict.get('repair_status', 'unknown')}")
    if verdict.get("confidence") is not None:
        lines.append(f"Confidence: {verdict['confidence']}")
    return redact_text("\n".join(lines))


def should_escalate(verdict: dict[str, Any]) -> bool:
    # BUG-1 fix: use the single canonical escalation set from supervisor_common
    # instead of a local list that had drifted (it omitted NEED_HUMAN_DECISION
    # and REFACTORING_REQUIRED, so those verdicts silently skipped the gate).
    status = str(verdict.get("status") or "").upper()
    return status in ESCALATION_STATUSES or status == INVALID_BOT2_STATUS


def escalation_message(
    *,
    session_id_value: str,
    task: str,
    bot1_result: str,
    evidence: str,
    verdict: dict[str, Any],
) -> str:
    risks = verdict.get("risks") or []
    fixes = verdict.get("required_fixes") or []
    return redact_text(
        f"""
Сообщение от Bot#2

Кратко: Bot#1 утверждает, что задачу можно принять, но Bot#2 нашёл проблему или остаточный риск.

Task:
{task}

Что утверждает Bot#1:
{bot1_result[:1200]}

Evidence:
{evidence[:1200]}

Что нашёл Bot#2:
{verdict.get('summary') or 'Bot#2 found a problem.'}

Риск:
{chr(10).join('- ' + str(risk) for risk in risks) if risks else '- not specified'}

Что нужно исправить / проверить:
{chr(10).join('- ' + str(fix) for fix in fixes) if fixes else '- not specified'}

Выбор:
Да — согласен с Bot#2, вернуть Bot#1 на исправление.
Нет — отклонить возражение Bot#2 и принять работу Bot#1 как есть.

MVP-команда для записи решения:
sudo /opt/hermes-assistant/scripts/bot2_gate.py decide {session_id_value} --choice yes --reason "..."
sudo /opt/hermes-assistant/scripts/bot2_gate.py decide {session_id_value} --choice no --reason "..."
""".strip()
    )


def escalate_if_needed(
    *,
    session_id_value: str,
    task: str,
    bot1_result: str,
    evidence: str,
    verdict: dict[str, Any],
    notify: bool = True,
    store_path: Path | str | None = None,
) -> None:
    if not should_escalate(verdict):
        return
    update_session(session_id_value, status="awaiting_human_decision", store_path=store_path)
    message = escalation_message(
        session_id_value=session_id_value,
        task=task,
        bot1_result=bot1_result,
        evidence=evidence,
        verdict=verdict,
    )
    add_event(
        session_id_value,
        "human_escalation",
        {
            "message": message,
            "choice_yes": "Согласен с Bot#2, вернуть Bot#1 на исправление",
            "choice_no": "Принять работу Bot#1 как есть",
            "verdict_status": verdict.get("status"),
        },
        store_path=store_path,
    )
    if notify:
        send_telegram(message)


def review_with_bot2_repair(
    *,
    task: str,
    acceptance: str,
    bot1_result: str,
    evidence: str,
    toolsets: str,
    timeout: int,
) -> tuple[int, str, dict[str, Any]]:
    prompt = bot2_prompt(task, acceptance, bot1_result, evidence)
    code, raw = run_hermes(prompt, toolsets=toolsets, timeout=timeout)
    verdict = parse_verdict(raw)
    if code != 0 and verdict.get("status") == "APPROVE":
        verdict["status"] = "NEEDS_HUMAN"
        verdict["risks"] = list(verdict.get("risks") or []) + [f"bot2_process_exit_{code}"]
    if verdict.get("status") != INVALID_BOT2_STATUS:
        return code, raw, verdict

    repair_code, repair_raw = run_hermes(
        repair_prompt(task, acceptance, bot1_result, raw),
        toolsets="",
        timeout=timeout,
    )
    repaired = parse_verdict(repair_raw)
    repaired["repair_attempted"] = True
    if repaired.get("status") != INVALID_BOT2_STATUS and repair_code == 0:
        repaired["repair_status"] = REPAIR_STATUS_REPAIRED
        return code, f"{raw}\n\n## Bot#2 JSON Repair\n\n{repair_raw}", repaired

    verdict["repair_attempted"] = True
    verdict["repair_status"] = REPAIR_STATUS_FAILED_CLOSED
    verdict["risks"] = list(verdict.get("risks") or []) + [f"bot2_repair_exit_{repair_code}"]
    return code, f"{raw}\n\n## Bot#2 JSON Repair Failed\n\n{repair_raw}", verdict


def run_review(
    *,
    mode: str,
    task: str,
    acceptance: str,
    bot1_result: str,
    evidence: str,
    toolsets: str,
    timeout: int = 600,
    no_telegram: bool = False,
    store_path: Path | str | None = None,
) -> str:
    sid = create_session(mode, task, acceptance, store_path=store_path)
    notify = not no_telegram

    devlog(
        sid,
        "Supervisor: review session created",
        f"Task:\n{task}\n\nAcceptance:\n{acceptance}",
        notify=notify,
        store_path=store_path,
    )
    add_round(sid, 1, "Bot#1", bot1_result, store_path=store_path)
    update_session(sid, bot1_result=bot1_result, evidence=evidence, status="bot1_submitted", store_path=store_path)

    devlog(
        sid,
        "Bot#2: review started",
        f"Toolsets: {toolsets or 'none'}",
        notify=notify,
        silent=True,
        store_path=store_path,
    )
    code, raw, verdict = review_with_bot2_repair(
        task=task,
        acceptance=acceptance,
        bot1_result=bot1_result,
        evidence=evidence,
        toolsets=toolsets,
        timeout=timeout,
    )
    add_round(sid, 2, "Bot#2", raw, store_path=store_path)
    if code != 0:
        verdict["risks"] = list(verdict.get("risks") or []) + [f"bot2_process_exit_{code}"]
    store_verdict(sid, verdict, raw, store_path=store_path)
    update_session(sid, status=str(verdict.get("status", "UNKNOWN")).lower(), store_path=store_path)
    devlog(sid, "Bot#2: verdict", verdict_text(verdict), notify=notify, store_path=store_path)
    escalate_if_needed(
        session_id_value=sid,
        task=task,
        bot1_result=bot1_result,
        evidence=evidence,
        verdict=verdict,
        notify=notify,
        store_path=store_path,
    )
    print(json.dumps({"session_id": sid, "verdict": redact_payload(verdict)}, ensure_ascii=False, indent=2))
    return sid


def read_arg_file(path: str | None) -> str:
    if not path:
        return ""
    return Path(path).read_text(encoding="utf-8")


def cmd_review(args: argparse.Namespace) -> None:
    task = args.task or read_arg_file(args.task_file)
    bot1_result = args.bot1_result or read_arg_file(args.bot1_result_file)
    evidence = args.evidence or read_arg_file(args.evidence_file) or bot1_result
    acceptance = args.acceptance or read_arg_file(args.acceptance_file) or "Review whether Bot#1 result satisfies the user task using evidence."
    if not task or not bot1_result:
        raise SystemExit("review requires --task and --bot1-result, or matching --*-file arguments")
    run_review(
        mode="manual",
        task=task,
        acceptance=acceptance,
        bot1_result=bot1_result,
        evidence=evidence,
        toolsets=args.toolsets,
        timeout=args.timeout,
        no_telegram=args.no_telegram,
        store_path=args.store,
    )


def cmd_status(args: argparse.Namespace) -> None:
    with db(args.store) as con:
        rows = con.execute(
            "SELECT id, created_at, mode, status, substr(task, 1, 100) AS task FROM bot2_review_sessions ORDER BY created_at DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
    for row in rows:
        print(f"{row['created_at']} {row['id']} {row['mode']} {row['status']} :: {row['task']}")


def cmd_show(args: argparse.Namespace) -> None:
    with db(args.store) as con:
        session = con.execute("SELECT * FROM bot2_review_sessions WHERE id=?", (args.session_id,)).fetchone()
        if not session:
            raise SystemExit(f"session not found: {args.session_id}")
        rounds = con.execute(
            "SELECT round_no, speaker, message FROM bot2_review_rounds WHERE session_id=? ORDER BY round_no, id",
            (args.session_id,),
        ).fetchall()
        verdicts = con.execute(
            "SELECT verdict_json, raw_output FROM bot2_verdicts WHERE session_id=? ORDER BY id",
            (args.session_id,),
        ).fetchall()
    print(json.dumps(redact_payload(dict(session)), ensure_ascii=False, indent=2))
    print("\nROUNDS")
    for row in rounds:
        print(f"\n[{row['round_no']}] {row['speaker']}\n{row['message']}")
    print("\nVERDICTS")
    for row in verdicts:
        print(row["verdict_json"])


def cmd_decide(args: argparse.Namespace) -> None:
    choice = args.choice.lower().strip()
    meaning = (
        "Согласен с Bot#2, вернуть Bot#1 на исправление"
        if choice == "yes"
        else "Отклонить возражение Bot#2 и принять работу Bot#1 как есть"
    )
    # BUG-2 fix: record the same canonical task status the supervisor uses, so
    # the review store and the supervisor task store speak one vocabulary.
    status = HUMAN_DECISION_YES_STATUS if choice == "yes" else HUMAN_DECISION_NO_STATUS
    with db(args.store) as con:
        exists = con.execute("SELECT id FROM bot2_review_sessions WHERE id=?", (args.session_id,)).fetchone()
        if not exists:
            raise SystemExit(f"session not found: {args.session_id}")
        con.execute(
            "INSERT INTO human_decisions(session_id, created_at, choice, meaning, reason) VALUES (?, ?, ?, ?, ?)",
            (args.session_id, utc_now(), choice, meaning, redact_text(args.reason or "")),
        )
        con.execute(
            "UPDATE bot2_review_sessions SET status=?, updated_at=? WHERE id=?",
            (status, utc_now(), args.session_id),
        )
        con.commit()
    add_event(args.session_id, "human_decision", {"choice": choice, "meaning": meaning, "reason": args.reason or ""}, store_path=args.store)
    send_telegram(f"[Hermes Bot#2 DevLog]\nHuman decision saved\nSession: {args.session_id}\n\n{choice.upper()}: {meaning}")
    print(json.dumps({"session_id": args.session_id, "choice": choice, "meaning": meaning, "status": status}, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Bot#2 Gate")
    parser.add_argument("--store", default=None, help="SQLite review store path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    review = sub.add_parser("review", help="Review a supplied Bot#1 result")
    review.add_argument("--task", default="")
    review.add_argument("--task-file")
    review.add_argument("--bot1-result", default="")
    review.add_argument("--bot1-result-file")
    review.add_argument("--evidence", default="")
    review.add_argument("--evidence-file")
    review.add_argument("--acceptance", default="")
    review.add_argument("--acceptance-file")
    review.add_argument("--toolsets", default="")
    review.add_argument("--timeout", type=int, default=600)
    review.add_argument("--no-telegram", action="store_true")
    review.set_defaults(func=cmd_review)

    status = sub.add_parser("status", help="List recent review sessions")
    status.add_argument("--limit", type=int, default=10)
    status.set_defaults(func=cmd_status)

    show = sub.add_parser("show", help="Show one review session")
    show.add_argument("session_id")
    show.set_defaults(func=cmd_show)

    decide = sub.add_parser("decide", help="Record human Да/Нет decision")
    decide.add_argument("session_id")
    decide.add_argument("--choice", required=True, choices=["yes", "no"])
    decide.add_argument("--reason", default="")
    decide.set_defaults(func=cmd_decide)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
