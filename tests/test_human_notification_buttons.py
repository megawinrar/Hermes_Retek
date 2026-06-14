from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import devlog  # noqa: E402
from human_notification import build_human_notification_buttons, dispatch_human_notification  # noqa: E402


def test_build_human_notification_buttons_use_compact_process_callbacks() -> None:
    buttons = build_human_notification_buttons({"process_id": "proc-20260614-052210-c45356"})

    rows = buttons["inline_keyboard"]
    assert rows[0][0]["callback_data"] == "hp:y:proc-20260614-052210-c45356"
    assert rows[0][1]["callback_data"] == "hp:n:proc-20260614-052210-c45356"
    assert rows[1][0]["callback_data"] == "hp:s:proc-20260614-052210-c45356"
    assert rows[1][1]["callback_data"] == "hp:t:proc-20260614-052210-c45356"
    assert rows[0][0]["text"] == "Да: вернуть Bot#1"
    assert rows[0][1]["text"] == "Нет: принять Bot#1"
    assert rows[1][0]["text"] == "Показать процесс"
    assert rows[1][1]["text"] == "Лог диалога"
    assert all(len(button["callback_data"].encode()) <= 64 for row in rows for button in row)


def test_telegram_chat_id_falls_back_to_allowed_user(monkeypatch) -> None:
    monkeypatch.delenv("BOT2_DEVLOG_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_SUPERVISOR_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_ALLOWED_USERS", "245167740, 123")

    assert devlog.telegram_chat_id() == "245167740"


def test_dispatch_human_notification_sends_buttons(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_send(text, *, reply_markup=None, parse_mode=None):
        calls.append({"text": text, "reply_markup": reply_markup, "parse_mode": parse_mode})
        return {"delivered": True, "message_id": 77, "error": ""}

    monkeypatch.setattr(devlog, "send_telegram_message", fake_send)

    delivery = dispatch_human_notification(
        {
            "kind": "human_decision_required",
            "process_id": "proc-1",
            "supervisor_task_id": "sup-1",
            "task": "deploy",
            "risk": "high",
            "bot1_version": "result",
            "bot2_version": "needs human",
            "recommendation": "ask user",
            "decision_semantics": {"yes": "return", "no": "accept"},
        },
        telegram=True,
        dry_run=False,
    )

    assert delivery["mode"] == "telegram_buttons"
    assert delivery["telegram_delivered"] is True
    assert delivery["buttons"] is True
    assert delivery["message_id"] == 77
    assert "[Hermes Supervisor: решение человека]" in calls[0]["text"]
    assert "Процесс: proc-1" in calls[0]["text"]
    assert "Да = return" in calls[0]["text"]
    assert calls[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "hp:y:proc-1"
