from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import supervisor_run as supervisor_run_cli  # noqa: E402
import supervisor_status as supervisor_status_cli  # noqa: E402
import supervisor_task as supervisor_task_cli  # noqa: E402


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def capture_json(func, args: SimpleNamespace) -> dict:
    output = io.StringIO()
    with redirect_stdout(output):
        func(args)
    return json.loads(output.getvalue())


def write_stub(path: Path, *, status: str = "APPROVE") -> None:
    verdict = {
        "status": status,
        "summary": "smoke approved" if status == "APPROVE" else "smoke requests changes",
        "evidence_checked": ["smoke evidence"],
        "risks": [] if status == "APPROVE" else ["smoke risk"],
        "required_fixes": [] if status == "APPROVE" else ["rerun smoke evidence"],
        "confidence": 0.95,
    }
    payload = {"session_id": "bot2-smoke-session", "verdict": verdict}
    path.write_text(
        """#!/usr/bin/env python3
import json
import sys

if "--no-telegram" not in sys.argv:
    raise SystemExit("smoke stub requires --no-telegram")

print(json.dumps(PAYLOAD))
""".replace("PAYLOAD", json.dumps(payload)),
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

    shown = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_status.py"),
        "--store",
        str(store),
        "show",
        task_id,
    )
    assert shown.returncode == 0, shown.stderr
    details = json.loads(shown.stdout)
    assert details["id"] == task_id
    assert details["bot2_links"][-1]["verdict"]["status"] == "APPROVE"


def test_supervisor_cli_modules_in_process_approved_flow(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    stub = tmp_path / "bot2_gate_stub.py"
    write_stub(stub)

    created = capture_json(
        supervisor_task_cli.cmd_create,
        SimpleNamespace(tz="In-process supervisor approved flow", tz_file=None, store=store),
    )
    task_id = created["task_id"]

    listed = capture_json(
        supervisor_status_cli.cmd_list,
        SimpleNamespace(limit=20, json=True, store=store),
    )
    assert listed[0]["id"] == task_id

    run = capture_json(
        supervisor_run_cli.cmd_run,
        SimpleNamespace(
            task_id=task_id,
            store=store,
            bot2_gate=stub,
            no_telegram=True,
            timeout=30,
            bot1_result="",
            evidence="",
        ),
    )
    assert run["status"] == "approved"
    assert run["bot2_verdict"]["status"] == "APPROVE"

    shown = capture_json(
        supervisor_status_cli.cmd_show,
        SimpleNamespace(task_id=task_id, store=store),
    )
    assert shown["id"] == task_id
    assert shown["role_runs"][-1]["role"] == "bot2"


def test_supervisor_cli_modules_in_process_human_decision_notifies_bot2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "supervisor.db"
    stub = tmp_path / "bot2_gate_reject_stub.py"
    write_stub(stub, status="REQUEST_CHANGES")

    created = capture_json(
        supervisor_task_cli.cmd_create,
        SimpleNamespace(tz="In-process supervisor human decision flow", tz_file=None, store=store),
    )
    task_id = created["task_id"]

    run = capture_json(
        supervisor_run_cli.cmd_run,
        SimpleNamespace(
            task_id=task_id,
            store=store,
            bot2_gate=stub,
            no_telegram=True,
            timeout=30,
            bot1_result="Bot1 wants to keep the change",
            evidence="pytest evidence",
        ),
    )
    assert run["status"] == "awaiting_human_decision"

    calls: list[dict[str, str | Path]] = []

    def fake_call_bot2_decide(*, bot2_gate: Path, session_id: str, choice: str, reason: str) -> None:
        calls.append(
            {
                "bot2_gate": bot2_gate,
                "session_id": session_id,
                "choice": choice,
                "reason": reason,
            }
        )

    monkeypatch.setattr(supervisor_task_cli, "call_bot2_decide", fake_call_bot2_decide)
    decided = capture_json(
        supervisor_task_cli.cmd_decide,
        SimpleNamespace(
            task_id=task_id,
            store=store,
            choice="no",
            reason="User accepts Bot1",
            bot2_gate=stub,
            skip_bot2_decide=False,
        ),
    )

    assert decided["status"] == "accepted_by_user_override"
    assert decided["bot2_session_id"] == "bot2-smoke-session"
    assert calls == [
        {
            "bot2_gate": stub,
            "session_id": "bot2-smoke-session",
            "choice": "no",
            "reason": "User accepts Bot1",
        }
    ]


def test_supervisor_cli_modules_in_process_error_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="create requires --tz or --tz-file"):
        supervisor_task_cli.cmd_create(SimpleNamespace(tz="", tz_file=None, store=tmp_path / "supervisor.db"))

    store = tmp_path / "supervisor.db"
    created = capture_json(
        supervisor_task_cli.cmd_create,
        SimpleNamespace(tz="Plain list output path", tz_file=None, store=store),
    )

    supervisor_status_cli.cmd_list(SimpleNamespace(limit=1, json=False, store=store))
    plain = capsys.readouterr().out
    assert created["task_id"] in plain
    assert "Plain list output path" in plain


def test_supervisor_task_create_from_file_and_plain_list(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    tz_file = tmp_path / "task.txt"
    tz_file.write_text("Create task from file and keep list output readable", encoding="utf-8")

    created = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_task.py"),
        "--store",
        str(store),
        "create",
        "--tz-file",
        str(tz_file),
    )
    assert created.returncode == 0, created.stderr
    task_id = json.loads(created.stdout)["task_id"]

    listed = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_status.py"),
        "--store",
        str(store),
        "list",
        "--limit",
        "1",
    )
    assert listed.returncode == 0, listed.stderr
    assert task_id in listed.stdout
    assert "Create task from file" in listed.stdout


def test_supervisor_task_create_requires_text(tmp_path: Path) -> None:
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_task.py"),
        "--store",
        str(tmp_path / "supervisor.db"),
        "create",
    )

    assert result.returncode != 0
    assert "create requires --tz or --tz-file" in result.stderr


def test_supervisor_task_decide_records_yes_without_bot2_side_effect(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    stub = tmp_path / "bot2_gate_reject_stub.py"
    write_stub(stub, status="REQUEST_CHANGES")

    created = run_cli(
        sys.executable,
        str(SCRIPTS / "supervisor_task.py"),
        "--store",
        str(store),
        "create",
        "--tz",
        "Smoke task that should return to Bot1 after human YES",
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
    assert json.loads(run.stdout)["status"] == "awaiting_human_decision"

    decided = run_cli(
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
        "--skip-bot2-decide",
    )
    assert decided.returncode == 0, decided.stderr
    payload = json.loads(decided.stdout)
    assert payload["status"] == "return_to_bot1"
    assert payload["choice"] == "yes"
    assert payload["bot2_session_id"] == "bot2-smoke-session"
