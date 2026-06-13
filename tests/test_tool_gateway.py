from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from supervisor_common import (  # noqa: E402
    connect,
    create_human_escalation,
    create_task,
    get_task,
    link_bot2,
    record_human_decision,
    update_task,
)


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def create_approved_task(store: Path, *, approved_action: str = "execute") -> str:
    task_id = create_task("Deploy production change with tests", store_path=store)["task_id"]
    link_bot2(
        task_id,
        "bot2-approved",
        {
            "status": "APPROVE",
            "approved_action": approved_action,
            "summary": "approved by test",
            "evidence_checked": ["pytest"],
            "risks": [],
            "required_fixes": [],
            "confidence": 0.95,
        },
        store_path=store,
    )
    update_task(task_id, status="running", store_path=store)
    update_task(task_id, status="approved" if approved_action == "execute" else "approved_refusal", store_path=store)
    return task_id


def test_gateway_blocks_git_push_without_supervisor_task(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "tool_gateway.py"),
        "--store",
        str(store),
        "check",
        "--",
        "git",
        "push",
        "origin",
        "main",
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["allowed"] is False
    assert payload["reason"] == "missing_supervisor_task_id"
    assert payload["risks"] == ["git_push"]


def test_gateway_blocks_approved_refusal_for_devops(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_approved_task(store, approved_action="refuse")

    result = run_cli(
        sys.executable,
        str(SCRIPTS / "tool_gateway.py"),
        "--store",
        str(store),
        "check",
        "--task-id",
        task_id,
        "--",
        "git",
        "push",
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["allowed"] is False
    assert payload["reason"] == "approved_refusal_does_not_unlock_devops"


def test_gateway_allows_linked_execute_approval_and_records_event(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_approved_task(store)

    result = run_cli(
        sys.executable,
        str(SCRIPTS / "tool_gateway.py"),
        "--store",
        str(store),
        "check",
        "--task-id",
        task_id,
        "--",
        "docker",
        "restart",
        "hermes-agent",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["allowed"] is True
    assert payload["reason"] == "linked_bot2_approval_to_execute"
    assert payload["risks"] == ["docker_restart"]

    task = get_task(task_id, store_path=store)
    with connect(store) as con:
        event = con.execute(
            "SELECT payload_json FROM supervisor_events WHERE task_id=? AND event_type='tool_gateway_decision'",
            (task["id"],),
        ).fetchone()
    assert event is not None
    assert json.loads(event["payload_json"])["allowed"] is True


def test_gateway_allows_explicit_user_override(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Deploy despite Bot2 objection", store_path=store)["task_id"]
    update_task(task_id, status="running", store_path=store)
    update_task(task_id, status="awaiting_human_decision", bot1_result="Bot1 says deploy", store_path=store)
    task = get_task(task_id, store_path=store)
    create_human_escalation(
        task,
        "bot2-reject",
        {
            "status": "REJECT",
            "summary": "Bot2 rejects deploy",
            "risks": ["deploy risk"],
            "required_fixes": ["ask user"],
        },
        store_path=store,
    )
    record_human_decision(task_id, "no", "User accepts Bot1", store_path=store)

    result = run_cli(
        sys.executable,
        str(SCRIPTS / "tool_gateway.py"),
        "--store",
        str(store),
        "check",
        "--task-id",
        task_id,
        "--",
        "sqlite3",
        "prod.db",
        "UPDATE users SET enabled=1",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["allowed"] is True
    assert payload["reason"] == "explicit_user_override"
    assert payload["risks"] == ["sqlite_write"]


def test_gateway_allows_safe_read_command_without_task(tmp_path: Path) -> None:
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "tool_gateway.py"),
        "--store",
        str(tmp_path / "supervisor.db"),
        "check",
        "--",
        "git",
        "status",
        "--short",
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["allowed"] is True
    assert payload["reason"] == "command_not_dangerous"
