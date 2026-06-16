#!/usr/bin/env python3
"""Offline smoke test for Kairos bot runtime."""

from __future__ import annotations

import asyncio

import kairos_bot


async def main() -> None:
    sent = []

    async def fake_api(method, **kwargs):
        sent.append((method, kwargs))
        return {"ok": True, "result": {"message_id": 1}}

    kairos_bot.api_call = fake_api
    chat_id = -424242
    db = kairos_bot.get_db(chat_id)
    await kairos_bot.process_message(
        chat_id,
        {
            "message_id": 101,
            "from": {"id": 7},
            "chat": {"id": chat_id},
            "text": "Нужно изготовить 160000 шт корпус чертеж Д16Т",
        },
        db,
    )
    task = db.execute("SELECT id, title, status FROM tasks ORDER BY id DESC LIMIT 1").fetchone()
    assert task is not None
    assert task["title"] == "Производственная задача"
    assert task["status"] == "pending"
    assert sent and sent[-1][0] == "sendMessage"
    assert sent[-1][1].get("reply_markup", {}).get("inline_keyboard")
    print(f"kairos_message_flow_ok task_id={task['id']}")


if __name__ == "__main__":
    asyncio.run(main())
