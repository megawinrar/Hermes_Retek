from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def test_process_approve_path(tmp_path: Path) -> None:
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
        "Проверь 2+2=4 коротко",
        "--acceptance",
        "Нужен короткий sanity answer",
        "--bot2-status",
        "APPROVE",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "approved"
    assert payload["route"]["task_level"] == "L1"

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
    assert {item["worker"] for item in details["assignments"]} >= {"router", "supervisor", "bot1", "tester", "bot2"}


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
        "Измени python code и deploy на production server",
        "--acceptance",
        "Нужны тесты, rollback и Bot2 review",
        "--bot2-status",
        "REJECT",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "awaiting_human_decision"
    assert "Версия Bot#1" in payload["human_message"]
    assert "Да —" in payload["human_message"]
    assert "Нет —" in payload["human_message"]
    assert payload["route"]["task_level"] == "L4"


def test_route_command_outputs_process_contract(tmp_path: Path) -> None:
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "route",
        "--task",
        "Составь чеклист backup restore SQLite",
    )
    assert result.returncode == 0, result.stderr
    route = json.loads(result.stdout)
    assert route["task_level"] == "L2"
    assert "process_plan" in route
