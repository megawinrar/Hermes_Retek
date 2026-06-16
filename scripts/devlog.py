#!/usr/bin/env python3
"""Small DevLog helper for Supervisor events and optional Telegram delivery."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from supervisor_common import add_event


TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


def telegram_chat_id() -> str:
    explicit = (
        os.environ.get("BOT2_DEVLOG_CHAT_ID")
        or os.environ.get("TELEGRAM_SUPERVISOR_CHAT_ID")
        or os.environ.get("TELEGRAM_CHAT_ID")
        or ""
    ).strip()
    if explicit:
        return explicit
    allowed_users = os.environ.get("TELEGRAM_ALLOWED_USERS", "")
    return next((item.strip() for item in allowed_users.split(",") if item.strip()), "")


def send_telegram_message(
    text: str,
    *,
    reply_markup: dict[str, Any] | None = None,
    parse_mode: str | None = None,
) -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = telegram_chat_id()
    if not token or not chat_id:
        return {"delivered": False, "error": "missing_telegram_token_or_chat_id", "chat_id": bool(chat_id)}
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    safe_text = text
    clipped = False
    if len(safe_text) > TELEGRAM_TEXT_LIMIT:
        safe_text = safe_text[: TELEGRAM_TEXT_LIMIT - 80].rstrip() + "\n\n...[truncated before Telegram limit]"
        clipped = True
    payload = {"chat_id": chat_id, "text": safe_text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if parse_mode and not clipped:
        payload["parse_mode"] = parse_mode
    result = subprocess.run(
        [
            "curl",
            "-sS",
            "--max-time",
            "20",
            "-X",
            "POST",
            url,
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload, ensure_ascii=False),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    response: dict[str, Any] = {}
    if result.stdout.strip():
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError:
            response = {"raw": result.stdout.strip()[:500]}
    delivered = result.returncode == 0 and bool(response.get("ok", result.returncode == 0))
    message = response.get("result") if isinstance(response.get("result"), dict) else {}
    return {
        "delivered": delivered,
        "chat_id": chat_id,
        "message_id": message.get("message_id"),
        "error": "" if delivered else response.get("description", result.stderr.strip()),
        "text_chars": len(text),
        "sent_text_chars": len(safe_text),
        "truncated": clipped,
    }


def send_telegram(text: str) -> bool:
    return bool(send_telegram_message(text).get("delivered"))


def send_telegram_document(
    path: str | Path,
    *,
    caption: str = "",
    filename: str = "",
) -> dict[str, Any]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = telegram_chat_id()
    artifact = Path(path)
    if not token or not chat_id:
        return {"delivered": False, "error": "missing_telegram_token_or_chat_id", "chat_id": bool(chat_id)}
    if not artifact.is_file():
        return {"delivered": False, "error": "file_not_found", "path": str(artifact)}
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    safe_caption = caption[:TELEGRAM_CAPTION_LIMIT].strip()
    curl_args = [
        "curl",
        "-sS",
        "--max-time",
        "60",
        "-X",
        "POST",
        url,
        "-F",
        f"chat_id={chat_id}",
        "-F",
        f"document=@{artifact};filename={filename or artifact.name}",
    ]
    if safe_caption:
        curl_args += ["-F", f"caption={safe_caption}"]
    result = subprocess.run(curl_args, text=True, capture_output=True, check=False)
    response: dict[str, Any] = {}
    if result.stdout.strip():
        try:
            response = json.loads(result.stdout)
        except json.JSONDecodeError:
            response = {"raw": result.stdout.strip()[:500]}
    delivered = result.returncode == 0 and bool(response.get("ok", result.returncode == 0))
    message = response.get("result") if isinstance(response.get("result"), dict) else {}
    document = message.get("document") if isinstance(message.get("document"), dict) else {}
    return {
        "delivered": delivered,
        "chat_id": chat_id,
        "message_id": message.get("message_id"),
        "file_id": document.get("file_id", ""),
        "path": str(artifact),
        "filename": filename or artifact.name,
        "size_bytes": artifact.stat().st_size,
        "caption_chars": len(caption),
        "sent_caption_chars": len(safe_caption),
        "error": "" if delivered else response.get("description", result.stderr.strip()),
    }


def cmd_send(args: argparse.Namespace) -> None:
    text = f"[Hermes Supervisor DevLog]\n{args.title}\nTask: {args.task_id}\n\n{args.body}"
    add_event(args.task_id, args.event_type, {"title": args.title, "body": args.body}, store_path=args.store)
    delivered = send_telegram(text) if args.telegram else False
    print(json.dumps({"task_id": args.task_id, "event_type": args.event_type, "telegram_delivered": delivered}, ensure_ascii=False))


def cmd_send_file(args: argparse.Namespace) -> None:
    payload = {
        "title": args.title,
        "path": args.path,
        "caption": args.caption,
        "filename": args.filename,
    }
    add_event(args.task_id, args.event_type, payload, store_path=args.store)
    delivery = (
        send_telegram_document(args.path, caption=args.caption or args.title, filename=args.filename)
        if args.telegram
        else {"delivered": False, "mode": "record_only"}
    )
    print(
        json.dumps(
            {
                "task_id": args.task_id,
                "event_type": args.event_type,
                "telegram_delivery": delivery,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Supervisor DevLog")
    parser.add_argument("--store", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    send = sub.add_parser("send", help="Store and optionally send a DevLog event")
    send.add_argument("--task-id", required=True)
    send.add_argument("--event-type", default="devlog")
    send.add_argument("--title", required=True)
    send.add_argument("--body", required=True)
    send.add_argument("--telegram", action="store_true")
    send.set_defaults(func=cmd_send)

    send_file = sub.add_parser("send-file", help="Store and optionally send a Telegram document")
    send_file.add_argument("--task-id", required=True)
    send_file.add_argument("--event-type", default="artifact")
    send_file.add_argument("--title", required=True)
    send_file.add_argument("--path", required=True)
    send_file.add_argument("--caption", default="")
    send_file.add_argument("--filename", default="")
    send_file.add_argument("--telegram", action="store_true")
    send_file.set_defaults(func=cmd_send_file)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
