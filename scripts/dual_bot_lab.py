#!/usr/bin/env python3
"""Run a two-model Hermes lab: Bot#1 on DeepSeek via Bothub, Bot#2 on Codex.

This script is intentionally separate from the live Hermes gateway. It reads the
existing Bothub OpenAI-compatible credentials, runs a controlled task, stores a
transcript, and prints a compact JSON summary.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import textwrap
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(os.environ.get("HERMES_PROJECT_DIR", "/opt/hermes-assistant"))
ENV_FILE = PROJECT_DIR / ".env"
CONFIG_FILE = PROJECT_DIR / "custom/config/config.yaml"
STORE_PATH = Path(
    os.environ.get(
        "DUAL_BOT_LAB_STORE",
        "/var/lib/docker/volumes/hermes-data/_data/dual_bot_lab_store.db",
    )
)
REPORT_DIR = PROJECT_DIR / "reports"
DEFAULT_BASE_URL = "https://openai.bothub.chat/v1"
DEFAULT_BOT1_MODEL = os.environ.get("BOT1_MODEL", "deepseek-v4-flash")
DEFAULT_BOT2_MODEL = os.environ.get("BOT2_MODEL", "gpt-5.3-codex")

BOT2_VERDICT_JSON_SCHEMA = """{
  "status": "APPROVE" | "APPROVE_WITH_EVIDENCE" | "REQUEST_CHANGES" | "REJECT" | "NEEDS_HUMAN" | "INSUFFICIENT_EVIDENCE" | "MISSING_TESTS_FOR_CODE_CHANGE" | "FAKE_IMPLEMENTATION_DETECTED" | "TEST_THEATER_DETECTED" | "RUBBER_STAMP_RISK" | "BLOCKED_BY_POLICY" | "LOOP_DETECTED",
  "approved_action": "execute" | "refuse" | "no_op" | "needs_human",
  "summary": "...",
  "evidence_checked": ["..."],
  "risks": ["..."],
  "required_fixes": ["..."],
  "confidence": 0.0
}"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_id() -> str:
    return f"dual-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def parse_simple_yaml_scalars(path: Path) -> dict[str, str]:
    """Tiny scalar reader for api_key/base_url fallback without PyYAML."""
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"^([A-Za-z0-9_]+):\s*(.+?)\s*$", raw)
        if match:
            data[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return data


def bothub_config() -> dict[str, str]:
    env = load_env_file()
    cfg = parse_simple_yaml_scalars(CONFIG_FILE)
    api_key = env.get("BOTHUB_API_KEY") or cfg.get("api_key") or os.environ.get("BOTHUB_API_KEY", "")
    base_url = env.get("BOTHUB_BASE_URL") or cfg.get("base_url") or os.environ.get("BOTHUB_BASE_URL", DEFAULT_BASE_URL)
    if not api_key:
        raise SystemExit("Bothub API key not found in .env, config, or BOTHUB_API_KEY")
    return {"api_key": api_key, "base_url": base_url.rstrip("/")}


def db() -> sqlite3.Connection:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STORE_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS dual_bot_runs (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            task TEXT NOT NULL,
            acceptance TEXT NOT NULL,
            bot1_model TEXT NOT NULL,
            bot2_model TEXT NOT NULL,
            status TEXT NOT NULL,
            report_path TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS dual_bot_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            speaker TEXT NOT NULL,
            model TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT DEFAULT '{}',
            FOREIGN KEY(run_id) REFERENCES dual_bot_runs(id)
        );
        """
    )
    con.commit()
    return con


def add_run(run_id_value: str, task: str, acceptance: str, bot1_model: str, bot2_model: str) -> None:
    with db() as con:
        con.execute(
            """
            INSERT INTO dual_bot_runs(id, created_at, task, acceptance, bot1_model, bot2_model, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id_value, utc_now(), task, acceptance, bot1_model, bot2_model, "created"),
        )
        con.commit()


def add_message(run_id_value: str, speaker: str, model: str, content: str, metadata: dict[str, Any] | None = None) -> None:
    with db() as con:
        con.execute(
            """
            INSERT INTO dual_bot_messages(run_id, created_at, speaker, model, content, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id_value, utc_now(), speaker, model, content, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        con.commit()


def update_run(run_id_value: str, status: str, report_path: str = "") -> None:
    with db() as con:
        con.execute(
            "UPDATE dual_bot_runs SET status=?, report_path=? WHERE id=?",
            (status, report_path, run_id_value),
        )
        con.commit()


def call_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    return call_chat_payload(base_url=base_url, api_key=api_key, payload=payload, timeout=timeout)


def call_chat_payload(*, base_url: str, api_key: str, payload: dict[str, Any], timeout: int) -> tuple[str, dict[str, Any]]:
    attempts = [dict(payload)]
    alt = dict(payload)
    alt["max_completion_tokens"] = alt.pop("max_tokens", 1600)
    attempts.append(alt)
    alt_no_temp = dict(alt)
    alt_no_temp.pop("temperature", None)
    attempts.append(alt_no_temp)

    errors: list[str] = []
    for body in attempts:
        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            errors.append(f"http_{exc.code}: {raw[:500]}")
            continue
        except urllib.error.URLError as exc:
            errors.append(f"url_error: {exc.reason}")
            continue
        except TimeoutError:
            errors.append("timeout")
            continue

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            errors.append(f"bad_json: {raw[:500]}")
            continue
        if "error" in data:
            errors.append(json.dumps(data["error"], ensure_ascii=False)[:500])
            continue
        content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        if content:
            return content, data
        errors.append(f"empty_content: {json.dumps(data, ensure_ascii=False)[:500]}")
    raise RuntimeError("Bothub chat completion failed: " + " | ".join(errors))


def bot1_messages(task: str, acceptance: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are Hermes Bot#1, the implementer. Be concrete and concise. "
                "Show public reasoning as short bullet assumptions/checks, not hidden chain-of-thought."
            ),
        },
        {
            "role": "user",
            "content": f"""
Task:
{task}

Acceptance criteria:
{acceptance}

Return Markdown with exactly these sections:
## Bot#1 Answer
## Public Reasoning
## Evidence
## Risks
""".strip(),
        },
    ]


def bot2_messages(task: str, acceptance: str, bot1_result: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are Hermes Bot#2, the independent Codex reviewer. "
                "Do not rubber-stamp. Provide public review notes and a JSON verdict. "
                "Do not reveal hidden chain-of-thought."
            ),
        },
        {
            "role": "user",
            "content": f"""
Task:
{task}

Acceptance criteria:
{acceptance}

Bot#1 result:
{bot1_result}

Return Markdown with:
## Bot#2 Review
## Public Reasoning
## Verdict JSON

Verdict JSON schema:
{BOT2_VERDICT_JSON_SCHEMA}
""".strip(),
        },
    ]


def bot2_repair_messages(task: str, acceptance: str, bot1_result: str, invalid_output: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are Hermes Bot#2 JSON repair. Return ONLY one valid JSON object. "
                "Do not include Markdown, fences, prose, logs, or explanations. "
                "If the original review lacks enough evidence, choose INSUFFICIENT_EVIDENCE or NEEDS_HUMAN."
            ),
        },
        {
            "role": "user",
            "content": f"""
Task:
{task}

Acceptance criteria:
{acceptance}

Bot#1 result:
{bot1_result}

Bot#2 invalid output to repair:
{invalid_output}

Return ONLY valid JSON matching this schema:
{BOT2_VERDICT_JSON_SCHEMA}
""".strip(),
        },
    ]


def write_report(
    *,
    run_id_value: str,
    task: str,
    acceptance: str,
    bot1_model: str,
    bot1_result: str,
    bot2_model: str,
    bot2_result: str,
) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"{run_id_value}.md"
    path.write_text(
        f"""# Dual Bot Lab Run

- Run: `{run_id_value}`
- Time: `{utc_now()}`
- Bot#1 model: `{bot1_model}`
- Bot#2 model: `{bot2_model}`

## Task

{task}

## Acceptance

{acceptance}

## Bot#1 Transcript

{bot1_result}

## Bot#2 Transcript

{bot2_result}
""",
        encoding="utf-8",
    )
    return path


def cmd_run(args: argparse.Namespace) -> None:
    cfg = bothub_config()
    rid = run_id()
    task = args.task.strip()
    acceptance = args.acceptance.strip()
    add_run(rid, task, acceptance, args.bot1_model, args.bot2_model)

    bot1, bot1_raw = call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=args.bot1_model,
        messages=bot1_messages(task, acceptance),
        max_tokens=args.max_tokens,
        timeout=args.timeout,
    )
    add_message(rid, "Bot#1", args.bot1_model, bot1, {"usage": bot1_raw.get("usage", {})})

    bot2, bot2_raw = call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=args.bot2_model,
        messages=bot2_messages(task, acceptance, bot1),
        max_tokens=args.max_tokens,
        timeout=args.timeout,
    )
    add_message(rid, "Bot#2", args.bot2_model, bot2, {"usage": bot2_raw.get("usage", {})})

    report = write_report(
        run_id_value=rid,
        task=task,
        acceptance=acceptance,
        bot1_model=args.bot1_model,
        bot1_result=bot1,
        bot2_model=args.bot2_model,
        bot2_result=bot2,
    )
    update_run(rid, "completed", str(report))
    print(
        json.dumps(
            {
                "run_id": rid,
                "status": "completed",
                "bot1_model": args.bot1_model,
                "bot2_model": args.bot2_model,
                "report_path": str(report),
                "bot1_preview": bot1[:600],
                "bot2_preview": bot2[:600],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def cmd_show(args: argparse.Namespace) -> None:
    with db() as con:
        run = con.execute("SELECT * FROM dual_bot_runs WHERE id=?", (args.run_id,)).fetchone()
        if not run:
            raise SystemExit(f"run not found: {args.run_id}")
        messages = con.execute(
            "SELECT created_at, speaker, model, content, metadata_json FROM dual_bot_messages WHERE run_id=? ORDER BY id",
            (args.run_id,),
        ).fetchall()
    data = dict(run)
    data["messages"] = [
        dict(row) | {"metadata": json.loads(row["metadata_json"] or "{}")} for row in messages
    ]
    for msg in data["messages"]:
        msg.pop("metadata_json", None)
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_list(args: argparse.Namespace) -> None:
    with db() as con:
        rows = con.execute(
            "SELECT id, created_at, bot1_model, bot2_model, status, report_path, substr(task,1,100) AS task FROM dual_bot_runs ORDER BY created_at DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
    print(json.dumps([dict(row) for row in rows], ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes dual Bot#1/Bot#2 lab runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Run Bot#1 on DeepSeek and Bot#2 on Codex")
    run.add_argument("--bot1-model", default=DEFAULT_BOT1_MODEL)
    run.add_argument("--bot2-model", default=DEFAULT_BOT2_MODEL)
    run.add_argument("--timeout", type=int, default=120)
    run.add_argument("--max-tokens", type=int, default=1800)
    run.add_argument(
        "--task",
        default="Design a safe rollout plan for enabling Bot#2 review before production deploys.",
    )
    run.add_argument(
        "--acceptance",
        default="Answer must include concrete steps, tests, rollback notes, and a clear risk decision.",
    )
    run.set_defaults(func=cmd_run)

    show = sub.add_parser("show", help="Show a previous run transcript")
    show.add_argument("run_id")
    show.set_defaults(func=cmd_show)

    list_cmd = sub.add_parser("list", help="List recent lab runs")
    list_cmd.add_argument("--limit", type=int, default=10)
    list_cmd.set_defaults(func=cmd_list)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
