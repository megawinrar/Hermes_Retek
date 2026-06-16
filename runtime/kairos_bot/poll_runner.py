#!/usr/bin/env python3
"""Polling runner for Kairos_Rbot."""

from __future__ import annotations

import asyncio
import json
import os
import sys

import aiohttp


sys.path.insert(0, "/opt/data/kairos-bot")
os.chdir("/opt/data/kairos-bot")

from kairos_bot import API_BASE, TOKEN, get_db, handle_callback_query, log, process_message, send_rich  # noqa: E402


async def poll() -> None:
    if not TOKEN:
        raise RuntimeError("KAIROS_TOKEN is not configured")
    offset = 0
    log.info("Kairos Bot starting in polling mode")
    async with aiohttp.ClientSession() as sess:
        async with sess.post(f"{API_BASE}/deleteWebhook", json={"drop_pending_updates": False}) as resp:
            log.info("deleteWebhook: %s", await resp.text())
        while True:
            try:
                params = {
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                }
                async with sess.get(
                    f"{API_BASE}/getUpdates",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=40),
                ) as resp:
                    data = await resp.json(content_type=None)
                if not data.get("ok"):
                    log.warning("getUpdates error: %s", data)
                    await asyncio.sleep(5)
                    continue
                for update in data.get("result", []):
                    offset = int(update["update_id"]) + 1
                    if "message" in update:
                        msg = update["message"]
                        chat_id = int(msg["chat"]["id"])
                        db = get_db(chat_id)
                        try:
                            await process_message(chat_id, msg, db)
                        except Exception as exc:
                            log.exception("Message processing error")
                            await send_rich(chat_id, f"⚠️ Ошибка: {exc}")
                    elif "callback_query" in update:
                        await handle_callback_query(update["callback_query"])
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Poll error: %s", exc)
                await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(poll())
