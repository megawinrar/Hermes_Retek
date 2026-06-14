from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import devlog  # noqa: E402
from human_notification import build_human_notification_buttons, dispatch_human_notification, format_human_notification  # noqa: E402


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
            "task_level": "L4",
            "task_type": "git_write_or_deploy",
            "risk_items": ["high"],
            "risk": "high",
            "bot1_version": "result",
            "bot2_version": "needs human",
            "missing_items": ["ask user"],
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
    assert "Hermes Supervisor" in calls[0]["text"]
    assert "Процесс: proc-1" in calls[0]["text"]
    assert "ЦИТАТА BOT#1\n> result" in calls[0]["text"]
    assert "ЦИТАТА BOT#2\n> needs human" in calls[0]["text"]
    assert "ЧЕГО НЕ ХВАТАЕТ ПО BOT#2\n1. ask user" in calls[0]["text"]
    assert "Да: return" in calls[0]["text"]
    assert "Decision commands" not in calls[0]["text"]
    assert calls[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "hp:y:proc-1"


def test_format_human_notification_reads_conflict_as_card() -> None:
    text = format_human_notification(
        {
            "process_id": "proc-1",
            "supervisor_task_id": "sup-1",
            "task_level": "L4",
            "task_type": "deploy",
            "task": "Deploy without tests",
            "bot1_version": "I will push to main now.",
            "bot2_version": "Do not push: tests are missing.",
            "missing_items": ["Run smoke tests", "Get explicit approval"],
            "risk_items": ["Production outage", "Unreviewed main push"],
            "decision_semantics": {"yes": "вернуть Bot#1", "no": "принять Bot#1"},
            "decision_commands": {"yes": "hidden", "no": "hidden"},
        }
    )

    assert "КОНФЛИКТ" in text
    assert "ЦИТАТА BOT#1\n> I will push to main now." in text
    assert "ЦИТАТА BOT#2\n> Do not push: tests are missing." in text
    assert "ЧЕГО НЕ ХВАТАЕТ ПО BOT#2\n1. Run smoke tests\n2. Get explicit approval" in text
    assert "РИСКИ\n1. Production outage\n2. Unreviewed main push" in text
    assert "Decision commands" not in text
