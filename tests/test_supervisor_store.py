from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def write_stub(path: Path, status: str) -> None:
    path.write_text(
        f"""#!/usr/bin/env python3
import json
import sys

cmd = sys.argv[1]
if cmd == "review":
    print(json.dumps({{
        "session_id": "bot2-test-session",
        "verdict": {{
            "status": "{status}",
            "summary": "stub {status}",
            "evidence_checked": ["stub evidence"],
            "risks": ["stub risk"] if "{status}" != "APPROVE" else [],
            "required_fixes": ["stub fix"] if "{status}" != "APPROVE" else [],
            "confidence": 0.9
        }}
    }}))
elif cmd == "decide":
    print(json.dumps({{"session_id": sys.argv[2], "status": "decided"}}))
else:
    raise SystemExit(2)
""",
        encoding="utf-8",
    )


def test_create_run_approve_and_show(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    stub = tmp_path / "bot2_gate_stub.py"
    write_stub(stub, "APPROVE")

    create = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_task.py"),
        "--store",
        str(store),
        "create",
        "--tz",
        "Build a safe MVP and verify evidence",
    )
    assert create.returncode == 0, create.stderr
    task_id = json.loads(create.stdout)["task_id"]

    run = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_run.py"),
        "--store",
        str(store),
        "--bot2-gate",
        str(stub),
        task_id,
    )
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result["status"] == "approved"
    assert result["bot2_session_id"] == "bot2-test-session"

    show = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_status.py"),
        "--store",
        str(store),
        "show",
        task_id,
    )
    assert show.returncode == 0, show.stderr
    details = json.loads(show.stdout)
    assert details["status"] == "approved"
    assert details["bot2_links"][0]["bot2_session_id"] == "bot2-test-session"
    assert {run["role"] for run in details["role_runs"]} >= {"developer", "tester", "bot2"}


def test_reject_creates_escalation_and_decision_paths(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    stub = tmp_path / "bot2_gate_stub.py"
    write_stub(stub, "REJECT")

    create = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_task.py"),
        "--store",
        str(store),
        "create",
        "--tz",
        "Change production code without enough evidence",
    )
    task_id = json.loads(create.stdout)["task_id"]

    run = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_run.py"),
        "--store",
        str(store),
        "--bot2-gate",
        str(stub),
        task_id,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["status"] == "awaiting_human_decision"

    decide_yes = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_task.py"),
        "--store",
        str(store),
        "decide",
        task_id,
        "--choice",
        "yes",
        "--reason",
        "Bot2 is right",
        "--bot2-gate",
        str(stub),
    )
    assert decide_yes.returncode == 0, decide_yes.stderr
    assert json.loads(decide_yes.stdout)["status"] == "return_to_bot1"

    show = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_status.py"),
        "--store",
        str(store),
        "show",
        task_id,
    )
    details = json.loads(show.stdout)
    assert details["status"] == "return_to_bot1"
    assert details["human_escalations"][0]["choice"] == "yes"


def test_decision_requires_open_human_escalation(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"

    create = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_task.py"),
        "--store",
        str(store),
        "create",
        "--tz",
        "Create a task but do not open a Bot2 dispute",
    )
    assert create.returncode == 0, create.stderr
    task_id = json.loads(create.stdout)["task_id"]

    decide = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_task.py"),
        "--store",
        str(store),
        "decide",
        task_id,
        "--choice",
        "no",
        "--reason",
        "Trying to bypass Bot2",
        "--skip-bot2-decide",
    )
    assert decide.returncode != 0
    assert "no pending human escalation" in decide.stderr

    show = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_status.py"),
        "--store",
        str(store),
        "show",
        task_id,
    )
    assert show.returncode == 0, show.stderr
    assert json.loads(show.stdout)["status"] == "created"
