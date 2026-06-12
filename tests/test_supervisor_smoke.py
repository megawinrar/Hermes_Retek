from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def write_stub(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import sys

if "--no-telegram" not in sys.argv:
    raise SystemExit("smoke stub requires --no-telegram")

print(json.dumps({
    "session_id": "bot2-smoke-session",
    "verdict": {
        "status": "APPROVE",
        "summary": "smoke approved",
        "evidence_checked": ["smoke evidence"],
        "risks": [],
        "required_fixes": [],
        "confidence": 0.95
    }
}))
""",
        encoding="utf-8",
    )


def test_cli_smoke_create_run_list(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    stub = tmp_path / "bot2_gate_stub.py"
    write_stub(stub)

    created = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_task.py"),
        "--store",
        str(store),
        "create",
        "--tz",
        "Smoke test Supervisor MVP without Telegram side effects",
    )
    assert created.returncode == 0, created.stderr
    task_id = json.loads(created.stdout)["task_id"]

    run = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_run.py"),
        "--store",
        str(store),
        "--bot2-gate",
        str(stub),
        "--no-telegram",
        task_id,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["status"] == "approved"

    listed = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_status.py"),
        "--store",
        str(store),
        "list",
        "--json",
    )
    assert listed.returncode == 0, listed.stderr
    rows = json.loads(listed.stdout)
    assert rows[0]["id"] == task_id
