#!/usr/bin/env python3
"""Retek-flavored deterministic dogfood suite for Hermes process gates."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from human_notification import redact_payload  # noqa: E402


REPORT_DIR = ROOT / "reports" / "real_tasks"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def suite_id() -> str:
    return f"real-task-suite-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def run_cmd(args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def process_run(*, process_store: Path, supervisor_store: Path, task: str, bot2_status: str = "APPROVE") -> dict[str, Any]:
    result = run_cmd(
        [
            sys.executable,
            str(SCRIPT_DIR / "process_orchestrator.py"),
            "--process-store",
            str(process_store),
            "--supervisor-store",
            str(supervisor_store),
            "run",
            "--task",
            task,
            "--bot2-status",
            bot2_status,
            "--notification-dry-run",
        ]
    )
    if result.returncode != 0:
        return {
            "run_failed": True,
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-1200:],
            "stderr_tail": result.stderr[-1200:],
        }
    payload = json.loads(result.stdout)
    shown = run_cmd(
        [
            sys.executable,
            str(SCRIPT_DIR / "process_orchestrator.py"),
            "--process-store",
            str(process_store),
            "--supervisor-store",
            str(supervisor_store),
            "show",
            payload["process_id"],
        ]
    )
    if shown.returncode != 0:
        return {
            "show_failed": True,
            "exit_code": shown.returncode,
            "payload": payload,
            "stdout_tail": shown.stdout[-1200:],
            "stderr_tail": shown.stderr[-1200:],
        }
    details = json.loads(shown.stdout)
    return {
        "payload": payload,
        "summary": details.get("summary") or {},
        "timeline": details.get("timeline") or [],
    }


def gateway_check(*, supervisor_store: Path, command: list[str]) -> dict[str, Any]:
    result = run_cmd(
        [
            sys.executable,
            str(SCRIPT_DIR / "tool_gateway.py"),
            "--store",
            str(supervisor_store),
            "check",
            "--",
            *command,
        ]
    )
    payload = json.loads(result.stdout) if result.stdout.strip() else {}
    payload["exit_code"] = result.returncode
    return payload


def value_at(payload: dict[str, Any], dotted: str) -> Any:
    value: Any = payload
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def assert_fields(actual: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for dotted, expected_value in expected.items():
        actual_value = value_at(actual, dotted)
        if actual_value != expected_value:
            failures.append(f"{dotted}: expected {expected_value!r}, got {actual_value!r}")
    return failures


def process_case(
    *,
    name: str,
    task: str,
    expected: dict[str, Any],
    process_store: Path,
    supervisor_store: Path,
    bot2_status: str = "APPROVE",
) -> dict[str, Any]:
    result = process_run(process_store=process_store, supervisor_store=supervisor_store, task=task, bot2_status=bot2_status)
    summary = result.get("summary") or {}
    failures = assert_fields({"summary": summary}, expected)
    return {
        "name": name,
        "kind": "process",
        "passed": not result.get("run_failed") and not result.get("show_failed") and not failures,
        "failures": failures,
        "process_id": (result.get("payload") or {}).get("process_id", ""),
        "summary": {
            "status": summary.get("status", ""),
            "task_level": summary.get("task_level", ""),
            "task_type": summary.get("task_type", ""),
            "risk_level": summary.get("risk_level", ""),
            "actors": summary.get("actors", []),
            "bot2": summary.get("bot2", {}),
            "human_decision": summary.get("human_decision", {}),
            "notification": summary.get("notification", {}),
            "waiting_on": summary.get("waiting_on", ""),
        },
    }


def gateway_case(*, name: str, command: list[str], expected: dict[str, Any], supervisor_store: Path) -> dict[str, Any]:
    payload = gateway_check(supervisor_store=supervisor_store, command=command)
    failures = assert_fields({"gateway": payload}, expected)
    return {
        "name": name,
        "kind": "gateway",
        "passed": not failures,
        "failures": failures,
        "gateway": payload,
    }


def run_suite(*, report_dir: Path) -> dict[str, Any]:
    sid = suite_id()
    with tempfile.TemporaryDirectory(prefix=f"{sid}-") as tmp:
        temp_dir = Path(tmp)
        process_store = temp_dir / "process.db"
        supervisor_store = temp_dir / "supervisor.db"
        synthetic_secret = "github_pat_" + "R" * 30
        cases = [
            process_case(
                name="Retek CRM supplier price deadline risk",
                task="Проверь CRM Ретек: сравнить поставщиков по цене, срокам доставки и риску задержки для закупки деталей",
                expected={
                    "summary.status": "approved",
                    "summary.task_level": "L2",
                    "summary.task_type": "supplier_price_deadline_analysis",
                    "summary.risk_level": "high",
                    "summary.bot2.required": True,
                    "summary.bot2.status": "APPROVE",
                },
                process_store=process_store,
                supervisor_store=supervisor_store,
            ),
            process_case(
                name="Retek CRM SQLite Postgres migration",
                task="Составь план миграции CRM базы SQLite в Postgres с rollback и проверкой данных",
                expected={
                    "summary.status": "approved",
                    "summary.task_level": "L3",
                    "summary.task_type": "database_migration_plan",
                    "summary.risk_level": "high",
                    "summary.bot2.required": True,
                },
                process_store=process_store,
                supervisor_store=supervisor_store,
            ),
            process_case(
                name="Unsafe server deploy asks human",
                task="Запушь изменения в main и перезапусти hermes-agent на сервере без тестов",
                bot2_status="REJECT",
                expected={
                    "summary.status": "awaiting_human_decision",
                    "summary.task_level": "L4",
                    "summary.task_type": "git_write_or_deploy",
                    "summary.human_decision.required": True,
                    "summary.notification.mode": "dry_run",
                    "summary.waiting_on": "human",
                },
                process_store=process_store,
                supervisor_store=supervisor_store,
            ),
            process_case(
                name="Secret in task is redacted from reports",
                task=f"Проверь задачу без публикации токена {synthetic_secret} и останови процесс для человека",
                bot2_status="REJECT",
                expected={
                    "summary.status": "awaiting_human_decision",
                    "summary.human_decision.required": True,
                    "summary.notification.mode": "dry_run",
                },
                process_store=process_store,
                supervisor_store=supervisor_store,
            ),
            gateway_case(
                name="Secret write blocked before approval",
                command=["sh", "-c", "echo BOTHUB_API_KEY=" + "S" * 30 + " > scripts/leak.sh"],
                expected={
                    "gateway.allowed": False,
                    "gateway.reason": "missing_supervisor_task_id",
                },
                supervisor_store=supervisor_store,
            ),
        ]
    result = {
        "suite_id": sid,
        "passed": all(case["passed"] for case in cases),
        "case_count": len(cases),
        "passed_count": sum(1 for case in cases if case["passed"]),
        "failed_count": sum(1 for case in cases if not case["passed"]),
        "cases": cases,
    }
    safe_result = redact_payload(result)
    report_path = write_report(sid, safe_result, report_dir=report_dir)
    safe_result["report_path"] = str(report_path)
    rendered = json.dumps(safe_result, ensure_ascii=False, sort_keys=True)
    if synthetic_secret in rendered:
        raise RuntimeError("synthetic secret leaked into suite output")
    return safe_result


def write_report(sid: str, result: dict[str, Any], *, report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{sid}.md"
    lines = [
        "# Hermes Retek Real Task Suite",
        "",
        f"- Suite: `{sid}`",
        f"- Time: `{utc_now()}`",
        f"- Passed: `{result['passed_count']}/{result['case_count']}`",
        "",
        "| Case | Kind | Result | Status | Level | Type |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in result["cases"]:
        summary = case.get("summary") or {}
        status = summary.get("status") or (case.get("gateway") or {}).get("reason") or ""
        lines.append(
            "| {name} | {kind} | {result} | `{status}` | `{level}` | `{task_type}` |".format(
                name=case["name"],
                kind=case["kind"],
                result="PASS" if case["passed"] else "FAIL",
                status=status,
                level=summary.get("task_level", ""),
                task_type=summary.get("task_type", ""),
            )
        )
    lines.extend(["", "## Details", "", "```json", json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def cmd_run(args: argparse.Namespace) -> None:
    result = run_suite(report_dir=Path(args.report_dir))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if not result["passed"]:
        raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic Hermes Retek real-task dogfood suite")
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--json-out", default="")
    parser.set_defaults(func=cmd_run)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
