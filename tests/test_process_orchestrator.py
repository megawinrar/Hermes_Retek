from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def test_process_l1_approve_path_without_bot2(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "run",
        "--task",
        "rewrite short hello",
        "--acceptance",
        "short answer",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "approved"
    assert payload["route"]["task_level"] == "L1"
    assert payload["bot2_session_id"] == ""
    assert payload["bot2_verdict"] == {}

    shown = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "show",
        payload["process_id"],
    )
    assert shown.returncode == 0, shown.stderr
    details = json.loads(shown.stdout)
    assert {item["worker"] for item in details["assignments"]} == {"router", "supervisor", "bot1"}


def test_process_reject_creates_human_escalation(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "run",
        "--task",
        "Change python code and deploy to production server",
        "--acceptance",
        "Need tests, rollback and Bot2 review",
        "--bot2-status",
        "REJECT",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "awaiting_human_decision"
    assert "Bot#1" in payload["human_message"]
    assert payload["route"]["task_level"] == "L4"


def test_route_command_outputs_process_contract(tmp_path: Path) -> None:
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "route",
        "--task",
        "Make backup restore checklist",
    )
    assert result.returncode == 0, result.stderr
    route = json.loads(result.stdout)
    assert route["task_level"] == "L2"
    assert "process_plan" in route


def test_l0_process_does_not_start_bot1_or_bot2(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "run",
        "--task",
        "status",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "approved"
    shown = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "show",
        payload["process_id"],
    )
    details = json.loads(shown.stdout)
    assert {item["worker"] for item in details["assignments"]} == {"router", "supervisor"}


def test_human_gate_blocks_approved_high_risk_deploy(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "run",
        "--task",
        "merge PR #12, push to main, and deploy production",
        "--bot2-status",
        "APPROVE",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "awaiting_human_decision"
    assert payload["route"]["human_gate_required"] is True
    assert payload["human_message"]

    shown = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "show",
        payload["process_id"],
    )
    details = json.loads(shown.stdout)
    notification_events = [event for event in details["events"] if event["event_type"] == "human_notification"]
    assert len(notification_events) == 1
    notification = notification_events[0]["payload"]["notification"]
    assert notification["process_id"] == payload["process_id"]
    assert notification["supervisor_task_id"] == payload["supervisor_task_id"]
    assert notification["risk"]
    assert notification["recommendation"]
    assert "yes" in notification["decision_semantics"]
    assert "no" in notification["decision_semantics"]


def test_invalid_bot2_output_fails_closed(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "run",
        "--task",
        "Change python code and add tests",
        "--bot2-status",
        "INVALID_BOT2_OUTPUT",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"


def test_human_notification_dry_run_payload_is_redacted(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    secret = "tok_" + "A" * 32
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "run",
        "--task",
        f"Change python code and deploy production with API_KEY='{secret}'",
        "--bot2-status",
        "NEEDS_HUMAN",
        "--notification-dry-run",
    )
    assert result.returncode == 0, result.stderr
    assert secret not in result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "awaiting_human_decision"
    assert payload["notification_delivery"]["mode"] == "dry_run"
    assert payload["human_notification"]["kind"] == "human_decision_required"
    assert payload["human_notification"]["task"] == "Change python code and deploy production with [REDACTED]"

    shown = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "show",
        payload["process_id"],
    )
    assert shown.returncode == 0, shown.stderr
    assert secret not in shown.stdout
    details = json.loads(shown.stdout)
    notification_events = [event for event in details["events"] if event["event_type"] == "human_notification"]
    assert len(notification_events) == 1
    assert notification_events[0]["payload"]["delivery"]["mode"] == "dry_run"
