#!/usr/bin/env python3
"""Human-gate notification payloads for Hermes process runs."""

from __future__ import annotations

from typing import Any

from secret_patterns import SECRET_PATTERNS, redact_payload, redact_text
from supervisor_common import YES_MEANING, NO_MEANING


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
        "decision_commands": {
            "yes": f"/opt/hermes-assistant/scripts/process_orchestrator.py decide {process_id} --choice yes --reason \"...\"",
            "no": f"/opt/hermes-assistant/scripts/process_orchestrator.py decide {process_id} --choice no --reason \"...\"",
        },
    }
    return redact_payload(payload)


def format_human_notification(payload: dict[str, Any]) -> str:
    semantics = payload.get("decision_semantics") or {}
    lines = [
        "[Hermes Supervisor: решение человека]",
        f"Процесс: {payload.get('process_id', '')}",
        f"Задача Supervisor: {payload.get('supervisor_task_id', '')}",
        f"Риск: {payload.get('risk', '')}",
        "",
        f"Задача: {payload.get('task', '')}",
        "",
        f"Версия Bot#1:\n{payload.get('bot1_version', '')}",
        "",
        f"Версия Bot#2:\n{payload.get('bot2_version', '')}",
        "",
        f"Рекомендация: {payload.get('recommendation', '')}",
        "",
        f"Да = {semantics.get('yes', '')}",
        f"Нет = {semantics.get('no', '')}",
    ]
    commands = payload.get("decision_commands") or {}
    if commands:
        lines.extend(
            [
                "",
                "Команды решения:",
                f"Да: {commands.get('yes', '')}",
                f"Нет: {commands.get('no', '')}",
            ]
        )
    return redact_text("\n".join(lines))


def build_human_notification_buttons(payload: dict[str, Any]) -> dict[str, Any]:
    process_id = str(payload.get("process_id") or "").strip()
    if not process_id:
        return {}
    return {
        "inline_keyboard": [
            [
                {"text": "Да: вернуть Bot#1", "callback_data": f"hp:y:{process_id}"},
                {"text": "Нет: принять Bot#1", "callback_data": f"hp:n:{process_id}"},
            ],
            [
                {"text": "Показать процесс", "callback_data": f"hp:s:{process_id}"},
                {"text": "Лог диалога", "callback_data": f"hp:t:{process_id}"},
            ],
        ]
    }


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
        from devlog import send_telegram_message

        buttons = build_human_notification_buttons(safe_payload)
        delivery = send_telegram_message(text, reply_markup=buttons or None)
        return {
            "mode": "telegram_buttons" if buttons else "telegram",
            "telegram_requested": True,
            "telegram_delivered": bool(delivery.get("delivered")),
            "text_chars": len(text),
            "buttons": bool(buttons),
            "message_id": delivery.get("message_id"),
            "error": delivery.get("error", ""),
        }
    except Exception as exc:  # pragma: no cover - defensive logging path
        return {
            "mode": "telegram",
            "telegram_requested": True,
            "telegram_delivered": False,
            "error": type(exc).__name__,
            "text_chars": len(text),
        }
