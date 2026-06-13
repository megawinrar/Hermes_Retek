#!/usr/bin/env python3
"""Fail-closed gateway for dangerous Hermes write/DevOps commands."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from human_notification import redact_payload, redact_text
from supervisor_common import (
    APPROVED_STATUSES,
    acquire_resource_locks,
    active_resource_lock,
    add_event,
    connect,
    get_task,
    loads,
    release_resource_locks,
)


ALLOWED_SUPERVISOR_STATUS = "approved"
USER_OVERRIDE_STATUS = "accepted_by_user_override"
REFUSAL_STATUS = "approved_refusal"

PROTECTED_PATH_MARKERS = {
    ".env",
    ".github/workflows",
    "auth",
    "ci",
    "config/production",
    "configs/production",
    "database",
    "db",
    "docker-compose",
    "migrations",
    "payment",
    "payments",
    "prod",
    "production",
    "secrets",
}
SECRET_WRITE_PATTERN = re.compile(
    r"(?:api[_-]?key|token|secret|password|private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s]{8,}",
    re.I,
)
SQL_WRITE_PATTERN = re.compile(r"\b(update|delete|drop|alter|insert|replace|truncate)\b", re.I)


def strip_remainder_dash(argv: list[str]) -> list[str]:
    return argv[1:] if argv and argv[0] == "--" else argv


def normalize_argv(argv: list[str]) -> list[str]:
    return [arg for arg in strip_remainder_dash(argv) if arg]


def effective_argv(argv: list[str]) -> list[str]:
    current = list(argv)
    while current:
        name = Path(current[0]).name.lower()
        if name in {"sudo", "command"}:
            current = current[1:]
            continue
        if name == "env":
            current = current[1:]
            while current and "=" in current[0] and not current[0].startswith("-"):
                current = current[1:]
            continue
        return current
    return []


def command_name(argv: list[str]) -> str:
    effective = effective_argv(argv)
    if not effective:
        return ""
    return Path(effective[0]).name.lower()


def command_text(argv: list[str]) -> str:
    return shlex.join(argv)


def looks_like_write_command(argv: list[str], joined_lower: str) -> bool:
    name = command_name(argv)
    if name in {"tee", "ed", "vim", "vi", "nano"}:
        return True
    if name in {"sed", "perl"} and any(arg.startswith("-i") or arg in {"-pi", "-pibak"} for arg in argv):
        return True
    if name in {"python", "python3"} and any(marker in joined_lower for marker in ["write_text", "open(", "sqlite3"]):
        return True
    if name in {"sh", "bash", "zsh"} and any(op in joined_lower for op in [" > ", ">>", "sed -i", " tee "]):
        return True
    return False


def classify_command(argv: list[str]) -> dict[str, Any]:
    argv = normalize_argv(argv)
    if not argv:
        return {"dangerous": False, "risks": [], "command": ""}

    joined = command_text(argv)
    joined_lower = joined.lower()
    name = command_name(argv)
    effective = effective_argv(argv)
    risks: list[str] = []

    if name == "git" and len(effective) > 1 and effective[1].lower() in {"push", "merge"}:
        risks.append(f"git_{effective[1].lower()}")
    if name == "git" and len(effective) > 1 and effective[1].lower() in {"tag"}:
        risks.append("git_release")
    if name == "docker" and len(effective) > 1 and effective[1].lower() == "restart":
        risks.append("docker_restart")
    if name == "docker" and " compose " in f" {joined_lower} " and any(word in joined_lower for word in [" up", " restart", " down"]):
        risks.append("docker_compose_runtime_change")
    if name == "kubectl" and any(word in effective[1:] for word in ["apply", "delete", "rollout", "scale", "set"]):
        risks.append("kubernetes_runtime_change")
    if "deploy" in joined_lower or "release" in joined_lower:
        risks.append("deploy_release")
    if name == "sqlite3" and SQL_WRITE_PATTERN.search(joined):
        risks.append("sqlite_write")
    if SECRET_WRITE_PATTERN.search(joined) and looks_like_write_command(argv, joined_lower):
        risks.append("secret_write")
    if looks_like_write_command(argv, joined_lower) and any(marker in joined_lower for marker in PROTECTED_PATH_MARKERS):
        risks.append("protected_config_or_domain_write")

    return {
        "dangerous": bool(risks),
        "risks": sorted(set(risks)),
        "command": redact_text(joined),
    }


def resources_for_risks(risks: list[str]) -> list[str]:
    resources: set[str] = set()
    for risk in risks:
        if risk.startswith("git_"):
            resources.add("git-write")
        if risk in {"deploy_release", "docker_restart", "docker_compose_runtime_change", "kubernetes_runtime_change"}:
            resources.add("runtime-deploy")
        if risk in {"sqlite_write"}:
            resources.add("database-write")
        if risk in {"secret_write", "protected_config_or_domain_write"}:
            resources.add("protected-config-write")
    return sorted(resources)


def lock_conflicts(resources: list[str], *, task_id: str, store_path: Path | str | None = None) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for resource in resources:
        lock = active_resource_lock(resource, store_path=store_path)
        if lock and str(lock.get("task_id") or "") != task_id:
            conflicts.append(lock)
    return conflicts


def latest_bot2_verdict(task_id: str, *, store_path: Path | str | None = None) -> dict[str, Any] | None:
    with connect(store_path) as con:
        row = con.execute(
            """
            SELECT verdict_json
            FROM supervisor_bot2_links
            WHERE task_id=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    if not row:
        return None
    verdict = loads(row["verdict_json"], {})
    return verdict if isinstance(verdict, dict) else None


def has_user_override_decision(task_id: str, *, store_path: Path | str | None = None) -> bool:
    with connect(store_path) as con:
        row = con.execute(
            """
            SELECT payload_json
            FROM supervisor_events
            WHERE task_id=? AND event_type='human_decision'
            ORDER BY id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    if not row:
        return False
    payload = loads(row["payload_json"], {})
    return isinstance(payload, dict) and str(payload.get("choice") or "").lower() == "no"


def approval_decision(
    *,
    task_id: str,
    classification: dict[str, Any],
    store_path: Path | str | None = None,
) -> dict[str, Any]:
    if not classification.get("dangerous"):
        return {"allowed": True, "reason": "command_not_dangerous"}

    if not task_id:
        return {"allowed": False, "reason": "missing_supervisor_task_id"}

    task = get_task(task_id, store_path=store_path)
    status = str(task.get("status") or "")
    if status == REFUSAL_STATUS:
        return {"allowed": False, "reason": "approved_refusal_does_not_unlock_devops", "task_status": status}

    if status == USER_OVERRIDE_STATUS:
        if has_user_override_decision(task_id, store_path=store_path):
            return {"allowed": True, "reason": "explicit_user_override", "task_status": status}
        return {"allowed": False, "reason": "user_override_missing_human_decision_event", "task_status": status}

    if status != ALLOWED_SUPERVISOR_STATUS:
        return {"allowed": False, "reason": "supervisor_task_not_approved", "task_status": status}

    verdict = latest_bot2_verdict(task_id, store_path=store_path)
    if not verdict:
        return {"allowed": False, "reason": "missing_linked_bot2_approval", "task_status": status}

    verdict_status = str(verdict.get("status") or "").upper()
    approved_action = str(verdict.get("approved_action") or "execute").lower()
    if verdict_status not in APPROVED_STATUSES:
        return {
            "allowed": False,
            "reason": "latest_bot2_verdict_not_approved",
            "task_status": status,
            "verdict_status": verdict_status,
        }
    if approved_action != "execute":
        return {
            "allowed": False,
            "reason": "bot2_approval_is_not_execute",
            "task_status": status,
            "verdict_status": verdict_status,
            "approved_action": approved_action,
        }
    return {
        "allowed": True,
        "reason": "linked_bot2_approval_to_execute",
        "task_status": status,
        "verdict_status": verdict_status,
        "approved_action": approved_action,
    }


def gateway_decision(
    *,
    task_id: str,
    argv: list[str],
    store_path: Path | str | None = None,
) -> dict[str, Any]:
    classification = classify_command(argv)
    decision = approval_decision(task_id=task_id, classification=classification, store_path=store_path)
    resources = resources_for_risks(list(classification.get("risks") or []))
    conflicts = lock_conflicts(resources, task_id=task_id, store_path=store_path)
    if decision.get("allowed") and conflicts:
        decision = {
            "allowed": False,
            "reason": "resource_lock_conflict",
            "lock_conflicts": conflicts,
        }
    payload = redact_payload(
        {
            "task_id": task_id,
            "command": classification.get("command", ""),
            "dangerous": classification.get("dangerous", False),
            "risks": classification.get("risks", []),
            "resources": resources,
            **decision,
        }
    )
    if task_id:
        try:
            add_event(task_id, "tool_gateway_decision", payload, store_path=store_path)
        except SystemExit:
            if classification.get("dangerous"):
                payload["allowed"] = False
                payload["reason"] = "supervisor_task_not_found"
    return payload


def cmd_check(args: argparse.Namespace) -> int:
    argv = normalize_argv(args.command)
    result = gateway_decision(task_id=args.task_id or "", argv=argv, store_path=args.store)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("allowed") else 2


def cmd_run(args: argparse.Namespace) -> int:
    argv = normalize_argv(args.command)
    if not argv:
        raise SystemExit("run requires a command after --")
    result = gateway_decision(task_id=args.task_id or "", argv=argv, store_path=args.store)
    if not result.get("allowed"):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 2
    resources = list(result.get("resources") or [])
    acquired = acquire_resource_locks(
        args.task_id or "",
        resources,
        reason=str(result.get("reason") or "tool_gateway_run"),
        command=str(result.get("command") or ""),
        store_path=args.store,
    )
    try:
        completed = subprocess.run(argv, text=True, capture_output=True, check=False)
        result["exit_code"] = completed.returncode
        result["stdout_chars"] = len(completed.stdout or "")
        result["stderr_chars"] = len(completed.stderr or "")
        result["stdout_preview"] = redact_text((completed.stdout or "")[:2000])
        result["stderr_preview"] = redact_text((completed.stderr or "")[:2000])
        print(json.dumps(redact_payload(result), ensure_ascii=False, indent=2, sort_keys=True))
        return completed.returncode
    finally:
        release_resource_locks(args.task_id or "", acquired, store_path=args.store)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Tool Gateway")
    parser.add_argument("--store", default=None, help="Supervisor SQLite store path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    check = sub.add_parser("check", help="Check whether a command may run")
    check.add_argument("--task-id", default="")
    check.add_argument("command", nargs=argparse.REMAINDER)
    check.set_defaults(func=cmd_check)

    run = sub.add_parser("run", help="Run a command only if the gateway allows it")
    run.add_argument("--task-id", default="")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=cmd_run)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
