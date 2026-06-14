"""Hermes tool adapter for the Retek process supervisor.

The live Hermes gateway imports files from ``/opt/hermes/tools`` and exposes
modules that call ``registry.register(...)``. This adapter keeps the upstream
Hermes loop unchanged: it shells out to the host-side Retek orchestrator and
returns a compact JSON summary to the model.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


class _NoopRegistry:
    def register(self, **_kwargs: Any) -> None:
        return None


try:
    from tools.registry import registry
except Exception:  # pragma: no cover - local tests run outside hermes-core
    registry = _NoopRegistry()


DEFAULT_PROJECT_DIR = Path(os.environ.get("HERMES_RETEK_PROJECT_DIR", "/opt/hermes-assistant"))
DEFAULT_ORCHESTRATOR = Path(
    os.environ.get(
        "HERMES_PROCESS_ORCHESTRATOR",
        str(DEFAULT_PROJECT_DIR / "scripts" / "process_orchestrator.py"),
    )
)
DEFAULT_PYTHON = os.environ.get("HERMES_PROCESS_PYTHON", sys.executable or "python3")
DEFAULT_PROCESS_STORE = os.environ.get("HERMES_PROCESS_STORE", "/opt/data/process_orchestrator_store.db")
DEFAULT_SUPERVISOR_STORE = os.environ.get("HERMES_SUPERVISOR_STORE", "/opt/data/supervisor_store.db")
DEFAULT_DUAL_BOT_STORE = os.environ.get("DUAL_BOT_LAB_STORE", "/opt/data/dual_bot_lab_store.db")
DEFAULT_DUAL_BOT_REPORT_DIR = os.environ.get("DUAL_BOT_REPORT_DIR", "/opt/data/reports")

JSON_OUTPUT_ACTIONS = {"route", "run", "show", "transcript", "decide"}
SUPPORTED_ACTIONS = sorted(JSON_OUTPUT_ACTIONS | {"events"})


def _json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def _as_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def check_requirements() -> bool:
    return DEFAULT_ORCHESTRATOR.exists()


def _base_command() -> list[str]:
    return [
        DEFAULT_PYTHON,
        str(DEFAULT_ORCHESTRATOR),
        "--process-store",
        DEFAULT_PROCESS_STORE,
        "--supervisor-store",
        DEFAULT_SUPERVISOR_STORE,
    ]


def build_command(args: dict[str, Any]) -> list[str]:
    action = _text(args.get("action"), "run").strip().lower()
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported action: {action}")

    cmd = _base_command()
    if action == "route":
        task = _text(args.get("task")).strip()
        if not task:
            raise ValueError("task is required for route")
        return cmd + ["route", "--task", task]

    if action == "run":
        task = _text(args.get("task")).strip()
        if not task:
            raise ValueError("task is required for run")
        cmd += [
            "run",
            "--task",
            task,
            "--acceptance",
            _text(args.get("acceptance"), "Result must satisfy the task with concrete evidence and risk notes."),
            "--bot1-model",
            _text(args.get("bot1_model"), "deepseek-v4-flash"),
            "--bot2-model",
            _text(args.get("bot2_model"), "gpt-5.3-codex"),
            "--timeout",
            str(_as_int(args.get("timeout"), 240, minimum=30, maximum=900)),
            "--max-tokens",
            str(_as_int(args.get("max_tokens"), 1400, minimum=256, maximum=6000)),
        ]
        if _as_bool(args.get("live_dual"), True):
            cmd.append("--live-dual")
        if _as_bool(args.get("live_route_audit"), True):
            cmd.append("--live-route-audit")
        if _as_bool(args.get("no_route_audit_cache"), False):
            cmd.append("--no-route-audit-cache")
        if _as_bool(args.get("notify_telegram"), False):
            cmd.append("--notify-telegram")
        if _as_bool(args.get("notification_dry_run"), True):
            cmd.append("--notification-dry-run")
        return cmd

    process_id = _text(args.get("process_id")).strip()
    if not process_id:
        raise ValueError(f"process_id is required for {action}")

    if action == "decide":
        choice = _text(args.get("choice")).strip().lower()
        if choice not in {"yes", "no"}:
            raise ValueError("choice must be yes or no")
        return cmd + [
            "decide",
            process_id,
            "--choice",
            choice,
            "--reason",
            _text(args.get("reason")),
        ]

    if action == "events":
        result = cmd + ["events", process_id]
        limit = _as_int(args.get("limit"), 20, minimum=0, maximum=200)
        if limit:
            result += ["--limit", str(limit)]
        return result

    return cmd + [action, process_id]


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("HERMES_PROJECT_DIR", str(DEFAULT_PROJECT_DIR))
    env.setdefault("PROCESS_STORE_PATH", DEFAULT_PROCESS_STORE)
    env.setdefault("SUPERVISOR_STORE_PATH", DEFAULT_SUPERVISOR_STORE)
    env.setdefault("DUAL_BOT_LAB_STORE", DEFAULT_DUAL_BOT_STORE)
    env.setdefault("DUAL_BOT_REPORT_DIR", DEFAULT_DUAL_BOT_REPORT_DIR)
    return env


def run_orchestrator(cmd: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=_subprocess_env(),
    )


def _parse_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        events.append(json.loads(stripped))
    return events


def _load_stdout(action: str, stdout: str) -> Any:
    if action == "events":
        return _parse_events(stdout)
    return json.loads(stdout) if stdout.strip() else {}


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value:
            return str(value)
    return ""


def summarize_payload(action: str, payload: Any, *, include_raw: bool = False) -> dict[str, Any]:
    if action == "events":
        return {
            "action": action,
            "event_count": len(payload),
            "events": payload,
        }

    if not isinstance(payload, dict):
        return {"action": action, "payload": payload}

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    route = summary.get("route") or payload.get("route") or payload.get("router") or {}
    bot2 = summary.get("bot2") or payload.get("bot2_verdict") or {}
    human = summary.get("human_decision") or payload.get("decision") or {}
    human_notification = payload.get("human_notification") if isinstance(payload.get("human_notification"), dict) else {}
    decision_semantics = human_notification.get("decision_semantics") or {}
    skills = summary.get("skills") or {}
    performance = summary.get("performance") or payload.get("performance") or {}
    next_action = summary.get("next_action") or payload.get("next_action") or {}
    status = _first_nonempty(summary.get("status"), payload.get("status"))
    human_required = bool(human.get("required") or status == "awaiting_human_decision" or human_notification)
    if "required" in bot2:
        bot2_required = bool(bot2.get("required"))
    else:
        bot2_required = bool(
            route.get("review_required")
            or route.get("human_gate_required")
            or route.get("task_level") in {"L3", "L4"}
        )
    bot2_session_id = _first_nonempty(bot2.get("session_id"), payload.get("bot2_session_id")) if bot2_required else ""

    result = {
        "action": action,
        "process_id": _first_nonempty(summary.get("process_id"), payload.get("process_id"), payload.get("id")),
        "supervisor_task_id": _first_nonempty(summary.get("supervisor_task_id"), payload.get("supervisor_task_id")),
        "status": status,
        "task_level": _first_nonempty(summary.get("task_level"), route.get("task_level")),
        "task_type": _first_nonempty(summary.get("task_type"), route.get("task_type")),
        "risk_level": _first_nonempty(summary.get("risk_level"), route.get("risk_level")),
        "bot2": {
            "required": bot2_required,
            "session_id": bot2_session_id,
            "status": _first_nonempty(bot2.get("status"), bot2.get("status")),
            "summary": _first_nonempty(bot2.get("summary")),
            "risks": bot2.get("risks", []),
            "review_cycle_count": int(bot2.get("review_cycle_count") or len(bot2.get("review_cycles") or [])),
            "repair_attempted": bool(bot2.get("repair_attempted")),
            "repair_status": _first_nonempty(bot2.get("repair_status")),
        },
        "human_decision": {
            "required": human_required,
            "status": _first_nonempty(
                human.get("status"),
                "awaiting_decision" if human_required and not human.get("choice") else "",
            ),
            "choice": human.get("choice"),
            "yes_meaning": _first_nonempty(human.get("yes_meaning"), decision_semantics.get("yes")),
            "no_meaning": _first_nonempty(human.get("no_meaning"), decision_semantics.get("no")),
        },
        "skills": {
            "selected": skills.get("selected", []),
            "gated": skills.get("gated", []),
            "roles": skills.get("roles", {}),
        },
        "performance": {
            "duration_ms": performance.get("duration_ms"),
            "route_audit": performance.get("route_audit", {}),
            "bot2": performance.get("bot2", {}),
        },
        "next_action": next_action,
    }
    if include_raw:
        result["raw"] = payload
    return result


def execute(**kwargs: Any) -> str:
    action = _text(kwargs.get("action"), "run").strip().lower()
    timeout = _as_int(kwargs.get("timeout"), 240, minimum=30, maximum=900) + 15
    include_raw = _as_bool(kwargs.get("include_raw"), False)
    try:
        cmd = build_command(kwargs)
        completed = run_orchestrator(cmd, timeout=timeout)
        if completed.returncode != 0:
            return _json(
                {
                    "ok": False,
                    "action": action,
                    "exit_code": completed.returncode,
                    "error": completed.stderr.strip() or completed.stdout.strip(),
                }
            )
        payload = _load_stdout(action, completed.stdout)
        summary = summarize_payload(action, payload, include_raw=include_raw)
        summary.update({"ok": True, "exit_code": 0})
        return _json(summary)
    except subprocess.TimeoutExpired as exc:
        return _json({"ok": False, "action": action, "error": "timeout", "timeout_seconds": exc.timeout})
    except Exception as exc:
        return _json({"ok": False, "action": action, "error": f"{type(exc).__name__}: {exc}"})


TOOL_SCHEMA = {
    "name": "hermes_process",
    "description": (
        "Run the Retek supervisor process loop from Telegram: route the task, "
        "run Bot#1/Bot#2 when needed, show logs, transcript, events, and record "
        "human yes/no decisions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": SUPPORTED_ACTIONS,
                "default": "run",
                "description": "run starts a process; decide records a human yes/no answer; show/transcript/events inspect it.",
            },
            "task": {"type": "string", "description": "User task or TZ for route/run."},
            "acceptance": {"type": "string", "description": "Acceptance criteria for Bot#1 and Bot#2."},
            "process_id": {"type": "string", "description": "Process id for show/transcript/events/decide."},
            "choice": {"type": "string", "enum": ["yes", "no"], "description": "Human decision for action=decide."},
            "reason": {"type": "string", "description": "Short reason for the human decision."},
            "live_dual": {"type": "boolean", "default": True, "description": "Use real Bot#1/Bot#2 LLM calls."},
            "live_route_audit": {"type": "boolean", "default": True, "description": "Let Bot#2 audit risky route classifications."},
            "no_route_audit_cache": {"type": "boolean", "default": False},
            "notify_telegram": {"type": "boolean", "default": False, "description": "Also send human-gate notification through DevLog Telegram settings."},
            "notification_dry_run": {"type": "boolean", "default": True},
            "bot1_model": {"type": "string", "default": "deepseek-v4-flash"},
            "bot2_model": {"type": "string", "default": "gpt-5.3-codex"},
            "timeout": {"type": "integer", "default": 240, "minimum": 30, "maximum": 900},
            "max_tokens": {"type": "integer", "default": 1400, "minimum": 256, "maximum": 6000},
            "limit": {"type": "integer", "default": 20, "minimum": 0, "maximum": 200},
            "include_raw": {"type": "boolean", "default": False},
        },
        "required": ["action"],
    },
}


registry.register(
    name="hermes_process",
    toolset="terminal",
    schema=TOOL_SCHEMA,
    handler=lambda args, **_kwargs: execute(**(args or {})),
    check_fn=check_requirements,
    description=TOOL_SCHEMA["description"],
    emoji="HP",
    max_result_size_chars=24000,
)
