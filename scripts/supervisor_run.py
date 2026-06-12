#!/usr/bin/env python3
"""Run the Supervisor MVP pipeline for one task."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from supervisor_common import (
    DEFAULT_BOT2_GATE,
    add_event,
    add_role_run,
    call_bot2_gate,
    create_human_escalation,
    escalation_text,
    get_task,
    link_bot2,
    supervisor_status_for_verdict,
    update_task,
)


def default_bot1_result(task: dict[str, object]) -> str:
    contract = task["acceptance_contract"]
    return (
        "Bot#1 MVP result:\n"
        "- Supervisor task was accepted for controlled review.\n"
        "- No production files were changed by this MVP run.\n"
        "- Evidence and acceptance contract are attached for Bot#2."
        f"\n\nAcceptance contract:\n{json.dumps(contract, ensure_ascii=False, indent=2)}"
    )


def default_evidence(task: dict[str, object], bot1_result: str) -> str:
    return (
        "Evidence package:\n"
        "- mode: Supervisor MVP dry-run\n"
        "- changed_files: none\n"
        "- tests: pending or provided externally\n"
        "- bot1_result follows\n\n"
        f"{bot1_result}"
    )


def cmd_run(args: argparse.Namespace) -> None:
    task = get_task(args.task_id, store_path=args.store)
    update_task(args.task_id, status="running", store_path=args.store)
    add_event(args.task_id, "pipeline_started", {"mode": "mvp"}, store_path=args.store)

    bot1_result = args.bot1_result or default_bot1_result(task)
    evidence = args.evidence or default_evidence(task, bot1_result)
    update_task(args.task_id, bot1_result=bot1_result, evidence=evidence, store_path=args.store)
    add_role_run(
        args.task_id,
        "developer",
        "completed",
        "Bot#1 result collected for MVP pipeline.",
        {"bot1_result_chars": len(bot1_result)},
        store_path=args.store,
    )
    add_role_run(
        args.task_id,
        "tester",
        "completed",
        "MVP tester evidence collected.",
        {"evidence_chars": len(evidence), "tests_mode": "external_or_dry_run"},
        store_path=args.store,
    )

    acceptance = "\n".join(task["acceptance_contract"].get("acceptance_criteria", []))
    session_id, verdict, raw = call_bot2_gate(
        bot2_gate=Path(args.bot2_gate),
        task=str(task["tz"]),
        acceptance=acceptance,
        bot1_result=bot1_result,
        evidence=evidence,
        no_telegram=args.no_telegram,
        timeout=args.timeout,
    )
    link_bot2(args.task_id, session_id, verdict, store_path=args.store)
    add_role_run(
        args.task_id,
        "bot2",
        "completed",
        f"Bot#2 verdict: {verdict.get('status', 'UNKNOWN')}",
        {"bot2_session_id": session_id, "raw_output_chars": len(raw), "verdict": verdict},
        store_path=args.store,
    )

    final_status = supervisor_status_for_verdict(verdict)
    update_task(args.task_id, status=final_status, store_path=args.store)
    add_event(
        args.task_id,
        "bot2_verdict",
        {"bot2_session_id": session_id, "verdict": verdict, "supervisor_status": final_status},
        store_path=args.store,
    )

    fresh_task = get_task(args.task_id, store_path=args.store)
    if final_status == "awaiting_human_decision":
        create_human_escalation(fresh_task, session_id, verdict, store_path=args.store)
        message = escalation_text(fresh_task, verdict)
        add_event(args.task_id, "human_escalation", {"message": message}, store_path=args.store)

    print(
        json.dumps(
            {
                "task_id": args.task_id,
                "status": final_status,
                "bot2_session_id": session_id,
                "bot2_verdict": verdict,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Supervisor pipeline runner")
    parser.add_argument("--store", default=None, help="SQLite store path")
    parser.add_argument("--bot2-gate", default=str(DEFAULT_BOT2_GATE))
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable Bot#2 Telegram DevLog/escalation messages for local tests",
    )
    parser.add_argument("task_id")
    parser.add_argument("--bot1-result", default="")
    parser.add_argument("--evidence", default="")
    parser.set_defaults(func=cmd_run)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
