#!/usr/bin/env python3
"""Create Supervisor tasks and record human Да/Нет decisions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from supervisor_common import (
    DEFAULT_BOT2_GATE,
    call_bot2_decide,
    create_task,
    get_task,
    record_human_decision,
)


def cmd_create(args: argparse.Namespace) -> None:
    tz = args.tz or (Path(args.tz_file).read_text(encoding="utf-8") if args.tz_file else "")
    if not tz.strip():
        raise SystemExit("create requires --tz or --tz-file")
    result = create_task(tz.strip(), store_path=args.store)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_decide(args: argparse.Namespace) -> None:
    get_task(args.task_id, store_path=args.store)
    decision = record_human_decision(args.task_id, args.choice, args.reason or "", store_path=args.store)
    session_id = decision.get("bot2_session_id")
    if session_id and not args.skip_bot2_decide:
        call_bot2_decide(
            bot2_gate=Path(args.bot2_gate),
            session_id=session_id,
            choice=args.choice,
            reason=args.reason or "",
        )
    decision["bot2_session_id"] = session_id
    print(json.dumps(decision, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Supervisor task CLI")
    parser.add_argument("--store", default=None, help="SQLite store path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    create = sub.add_parser("create", help="Create a Supervisor task from TZ")
    create.add_argument("--tz", default="")
    create.add_argument("--tz-file")
    create.set_defaults(func=cmd_create)

    decide = sub.add_parser("decide", help="Record human Да/Нет decision")
    decide.add_argument("task_id")
    decide.add_argument("--choice", required=True, choices=["yes", "no"], help="yes=Да, agree with Bot#2; no=Нет, accept Bot#1")
    decide.add_argument("--reason", default="")
    decide.add_argument("--bot2-gate", default=str(DEFAULT_BOT2_GATE))
    decide.add_argument("--skip-bot2-decide", action="store_true", help="Only update Supervisor store")
    decide.set_defaults(func=cmd_decide)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
