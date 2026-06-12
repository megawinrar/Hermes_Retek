#!/usr/bin/env python3
"""Small DevLog helper for Supervisor events and optional Telegram delivery."""

from __future__ import annotations

import argparse
import json
import os
import subprocess

from supervisor_common import add_event


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("BOT2_DEVLOG_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
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
    return result.returncode == 0


def cmd_send(args: argparse.Namespace) -> None:
    text = f"[Hermes Supervisor DevLog]\n{args.title}\nTask: {args.task_id}\n\n{args.body}"
    add_event(args.task_id, args.event_type, {"title": args.title, "body": args.body}, store_path=args.store)
    delivered = send_telegram(text) if args.telegram else False
    print(json.dumps({"task_id": args.task_id, "event_type": args.event_type, "telegram_delivered": delivered}, ensure_ascii=False))


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
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
