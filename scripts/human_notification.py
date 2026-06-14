#!/usr/bin/env python3
"""Human-gate notification payloads for Hermes process runs."""

from __future__ import annotations

from html import escape
from typing import Any

from secret_patterns import SECRET_PATTERNS, redact_payload, redact_text
from supervisor_common import YES_MEANING, NO_MEANING


def _join_items(items: Any, default: str) -> str:
    if isinstance(items, list) and items:
        return "; ".join(str(item) for item in items)
    if isinstance(items, str) and items.strip():
        return items.strip()
    return default


def _list_items(items: Any, fallback: str = "") -> list[str]:
    if isinstance(items, list):
        return [str(item).strip() for item in items if str(item).strip()]
    if isinstance(items, str) and items.strip():
        return [item.strip() for item in items.split(";") if item.strip()]
    if fallback.strip():
        return [fallback.strip()]
    return []


def _clip_text(value: Any, limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def _quote_block(value: Any, limit: int = 900) -> list[str]:
    text = _clip_text(value, limit=limit) or "Нет текста."
    return ["> " + line if line else ">" for line in text.splitlines()]


def _numbered_block(items: list[str], *, empty: str, limit: int = 5) -> list[str]:
    if not items:
        return [f"1. {empty}"]
    return [f"{idx}. {_clip_text(item, 260)}" for idx, item in enumerate(items[:limit], start=1)]


def _bot2_arguments(verdict: dict[str, Any]) -> list[str]:
    result: list[str] = []
    summary = str(verdict.get("summary") or "").strip()
    if summary:
        result.append(summary)
    for item in _list_items(verdict.get("evidence_checked"))[:3]:
        result.append(f"Проверено: {item}")
    for item in _list_items(verdict.get("risks"))[:3]:
        result.append(f"Риск: {item}")
    for item in _list_items(verdict.get("required_fixes"))[:3]:
        result.append(f"Не хватает: {item}")
    return result


def _html(value: Any) -> str:
    return escape(str(value or ""), quote=False)


def _html_quote(value: Any, limit: int = 900) -> str:
    text = _clip_text(value, limit=limit) or "Нет текста."
    return "<blockquote>" + _html(text) + "</blockquote>"


def _html_bullets(items: list[str], *, empty: str, limit: int = 5) -> list[str]:
    source = items[:limit] if items else [empty]
    return [f"• {_html(_clip_text(item, 260))}" for item in source]


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
        "risk_level": route.get("risk_level", ""),
        "human_gate_required": bool(route.get("human_gate_required")),
        "bot1_version": str(task.get("bot1_result") or "Bot#1 result is empty."),
        "bot2_version": str(verdict.get("summary") or "Bot#2 did not provide a summary."),
        "risk_items": _list_items(verdict.get("risks")),
        "risk": _join_items(verdict.get("risks"), "No explicit risk listed."),
        "missing_items": _list_items(verdict.get("required_fixes")),
        "recommendation": _join_items(verdict.get("required_fixes"), "Ask user before continuing."),
        "bot2_arguments": _bot2_arguments(verdict),
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
    bot1_version = payload.get("bot1_version", "")
    bot2_version = payload.get("bot2_version", "")
    risk_items = _list_items(payload.get("risk_items"), str(payload.get("risk") or ""))
    missing_items = _list_items(payload.get("missing_items"), str(payload.get("recommendation") or ""))
    bot2_arguments = _list_items(payload.get("bot2_arguments"), str(bot2_version or ""))
    lines = [
        "<b>Hermes Supervisor</b>",
        "<b>Выберите сторону конфликта</b>",
        "",
        f"<b>Процесс:</b> <code>{_html(payload.get('process_id', ''))}</code>",
        f"<b>Supervisor:</b> <code>{_html(payload.get('supervisor_task_id', ''))}</code>",
        f"<b>Уровень:</b> {_html(payload.get('task_level', ''))} / <b>риск:</b> {_html(payload.get('risk_level', ''))}",
        f"<b>Тип:</b> {_html(payload.get('task_type', ''))}",
        "",
        "<b>Задача</b>",
        _html_quote(payload.get("task", ""), 600),
        "",
        "<b>Конфликт</b>",
        "Bot#1 предлагает действие. Bot#2 видит риск и просит выбрать сторону.",
        "",
        "<b>Позиция Bot#1</b>",
        _html_quote(bot1_version),
        "",
        "<b>Позиция Bot#2</b>",
        _html_quote(bot2_version),
        "",
        "<b>Аргументы Bot#2</b>",
        *_html_bullets(bot2_arguments, empty="Bot#2 не указал отдельные аргументы, но требует ручное решение.", limit=7),
        "",
        "<b>Что нужно закрыть по Bot#2</b>",
        *_html_bullets(missing_items, empty="Bot#2 не указал отдельные исправления.", limit=5),
        "",
        "<b>Риски</b>",
        *_html_bullets(risk_items, empty="Bot#2 не указал отдельные риски.", limit=5),
        "",
        "<b>Кнопки</b>",
        f"Выбрать Bot#2 — {_html(semantics.get('yes', ''))}",
        f"Выбрать Bot#1 — {_html(semantics.get('no', ''))}",
    ]
    return redact_text("\n".join(lines))


def build_human_notification_buttons(payload: dict[str, Any]) -> dict[str, Any]:
    process_id = str(payload.get("process_id") or "").strip()
    if not process_id:
        return {}
    return {
        "inline_keyboard": [
            [
                {"text": "Выбрать Bot#2", "callback_data": f"hp:y:{process_id}"},
                {"text": "Выбрать Bot#1", "callback_data": f"hp:n:{process_id}"},
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
        delivery = send_telegram_message(text, reply_markup=buttons or None, parse_mode="HTML")
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
