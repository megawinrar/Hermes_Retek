#!/usr/bin/env python3
"""Human-gate notification payloads for Hermes process runs."""

from __future__ import annotations

import re
from typing import Any

from supervisor_common import YES_MEANING, NO_MEANING

SECRET_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9_.-]{20,}", re.I),
    re.compile(r"\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*['\"]?[A-Za-z0-9_.:-]{20,}['\"]?", re.I),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----.*?-----END (?:RSA |OPENSSH |EC )?PRIVATE KEY-----", re.S),
]


def redact_text(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_payload(item) for key, item in value.items()}
    return value


def _join_items(items: Any, default: str) -> str:
    if isinstance(items, list) and items:
        return "; ".join(str(item) for item in items)
    if isinstance(items, str) and items.strip():
        return items.strip()
    return default


def build_human_notification_payload(
    *,
    process_id: str,
    supervisor_task_id: str,
    task: dict[str, Any],
    route: dict[str, Any],
    bot2_session_id: str,
    verdict: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "kind": "human_decision_required",
        "process_id": process_id,
        "supervisor_task_id": supervisor_task_id,
        "bot2_session_id": bot2_session_id,
        "task": str(task.get("tz") or ""),
        "task_level": route.get("task_level", ""),
        "task_type": route.get("task_type", ""),
        "human_gate_required": bool(route.get("human_gate_required")),
        "bot1_version": str(task.get("bot1_result") or "Bot#1 result is empty."),
        "bot2_version": str(verdict.get("summary") or "Bot#2 did not provide a summary."),
        "risk": _join_items(verdict.get("risks"), "No explicit risk listed."),
        "recommendation": _join_items(verdict.get("required_fixes"), "Ask user before continuing."),
        "decision_semantics": {
            "yes": YES_MEANING,
            "no": NO_MEANING,
        },
    }
    return redact_payload(payload)


def format_human_notification(payload: dict[str, Any]) -> str:
    semantics = payload.get("decision_semantics") or {}
    lines = [
        "[Hermes Human Gate]",
        f"Process: {payload.get('process_id', '')}",
        f"Supervisor task: {payload.get('supervisor_task_id', '')}",
        f"Risk: {payload.get('risk', '')}",
        "",
        f"Task: {payload.get('task', '')}",
        "",
        f"Bot#1 version:\n{payload.get('bot1_version', '')}",
        "",
        f"Bot#2 version:\n{payload.get('bot2_version', '')}",
        "",
        f"Recommendation: {payload.get('recommendation', '')}",
        "",
        f"YES = {semantics.get('yes', '')}",
        f"NO = {semantics.get('no', '')}",
    ]
    return redact_text("\n".join(lines))


def dispatch_human_notification(
    payload: dict[str, Any],
    *,
    telegram: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    safe_payload = redact_payload(payload)
    text = format_human_notification(safe_payload)
    if dry_run:
        return {"mode": "dry_run", "telegram_requested": bool(telegram), "telegram_delivered": False, "text_chars": len(text)}
    if not telegram:
        return {"mode": "record_only", "telegram_requested": False, "telegram_delivered": False, "text_chars": len(text)}
    try:
        from devlog import send_telegram

        delivered = bool(send_telegram(text))
        return {"mode": "telegram", "telegram_requested": True, "telegram_delivered": delivered, "text_chars": len(text)}
    except Exception as exc:  # pragma: no cover - defensive logging path
        return {
            "mode": "telegram",
            "telegram_requested": True,
            "telegram_delivered": False,
            "error": type(exc).__name__,
            "text_chars": len(text),
        }
