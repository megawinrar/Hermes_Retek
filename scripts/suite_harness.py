#!/usr/bin/env python3
"""Shared building blocks for the deterministic process/gateway suites.

real_task_suite and stage2_battle_suite were ~60-70% duplicated. The genuinely
identical pieces live here; each suite keeps its own case list, process-result
shape, and report format (those legitimately differ).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import process_orchestrator as orchestrator
import tool_gateway


DEFAULT_ACCEPTANCE = "Result must satisfy the task with concrete evidence and risk notes."
DEFAULT_BOT1_MODEL = "deepseek-v4-flash"
DEFAULT_BOT2_MODEL = "gpt-5.3-codex"
DEFAULT_TIMEOUT = 120
DEFAULT_MAX_TOKENS = 1400


def build_process_args(
    *,
    process_store: Any,
    supervisor_store: Any,
    task: str,
    bot2_status: str = "APPROVE",
) -> SimpleNamespace:
    """The run_process argument namespace shared verbatim by both suites."""
    return SimpleNamespace(
        process_store=process_store,
        supervisor_store=supervisor_store,
        task=task,
        acceptance=DEFAULT_ACCEPTANCE,
        bot1_result="",
        evidence="",
        bot2_status=bot2_status,
        bot2_verdict_json="",
        bot2_route_audit_json="",
        live_route_audit=False,
        live_dual=False,
        bot1_model=DEFAULT_BOT1_MODEL,
        bot2_model=DEFAULT_BOT2_MODEL,
        timeout=DEFAULT_TIMEOUT,
        max_tokens=DEFAULT_MAX_TOKENS,
        notify_telegram=False,
        notification_dry_run=True,
    )


def run_process_and_details(args: SimpleNamespace) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the orchestrator and load its process details.

    Raises on failure; each suite wraps this in its own try/except because they
    present errors differently.
    """
    payload = orchestrator.run_process(args)
    details = orchestrator.process_details(
        payload["process_id"],
        store_path=args.process_store,
        supervisor_store_path=args.supervisor_store,
    )
    return payload, details


def gateway_check(*, supervisor_store: Any, command: list[str], task_id: str = "") -> dict[str, Any]:
    payload = tool_gateway.gateway_decision(task_id=task_id, argv=command, store_path=supervisor_store)
    payload["exit_code"] = 0 if payload.get("allowed") else 2
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


def gateway_case(*, name: str, command: list[str], expected: dict[str, Any], supervisor_store: Any) -> dict[str, Any]:
    payload = gateway_check(supervisor_store=supervisor_store, command=command)
    failures = assert_fields({"gateway": payload}, expected)
    return {
        "name": name,
        "kind": "gateway",
        "passed": not failures,
        "failures": failures,
        "gateway": payload,
    }


# --- markdown report helpers ---


def report_header(title: str, sid: str, *, passed_count: int, case_count: int, time: str) -> list[str]:
    return [
        f"# {title}",
        "",
        f"- Suite: `{sid}`",
        f"- Time: `{time}`",
        f"- Passed: `{passed_count}/{case_count}`",
        "",
    ]


def case_status(case: dict[str, Any]) -> str:
    """The status shown in a report row: process status, else gateway reason."""
    return (case.get("summary") or {}).get("status") or (case.get("gateway") or {}).get("reason") or ""


def json_block(obj: Any) -> list[str]:
    return ["```json", json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), "```"]
