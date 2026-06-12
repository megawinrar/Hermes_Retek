#!/usr/bin/env python3
"""List and inspect Supervisor MVP tasks."""

from __future__ import annotations

import argparse
import json

from supervisor_common import list_tasks, task_details


def cmd_list(args: argparse.Namespace) -> None:
    rows = list_tasks(args.limit, store_path=args.store)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    for row in rows:
        print(f"{row['created_at']} {row['id']} {row['risk_level']} {row['status']} :: {row['tz']}")


def cmd_show(args: argparse.Namespace) -> None:
    print(json.dumps(task_details(args.task_id, store_path=args.store), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Supervisor status CLI")
    parser.add_argument("--store", default=None, help="SQLite store path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_cmd = sub.add_parser("list", help="List recent Supervisor tasks")
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="Show task details")
    show.add_argument("task_id")
    show.set_defaults(func=cmd_show)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
