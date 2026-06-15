from __future__ import annotations

import json
import os
import subprocess
import sys
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from supervisor_common import create_task, update_task  # noqa: E402


SAFE_RESTART = SCRIPTS / "hermes_safe_restart.sh"


def run_safe_restart(tmp_path: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    audit = tmp_path / "hermes-restarts.log"
    command = [
        "bash",
        str(SAFE_RESTART),
        "--audit-log",
        str(audit),
        "--lock-dir",
        str(tmp_path / "restart.lock"),
        "--supervisor-store",
        str(tmp_path / "supervisor.db"),
        "--process-store",
        str(tmp_path / "process.db"),
        *args,
    ]
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(command, text=True, capture_output=True, check=False, env=merged_env)


def audit_lines(tmp_path: Path) -> list[dict[str, object]]:
    text = (tmp_path / "hermes-restarts.log").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_safe_restart_dry_run_writes_audit_without_docker(tmp_path: Path) -> None:
    result = run_safe_restart(tmp_path, "--dry-run", "--reason", "unit-test")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["event"] == "restart_skipped"
    assert payload["status"] == "dry_run"
    assert payload["reason"] == "unit-test"
    assert payload["dry_run"] is True
    assert audit_lines(tmp_path)[-1] == payload


def test_safe_restart_blocks_when_supervisor_task_is_active(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Deploy production change", store_path=store)["task_id"]
    update_task(task_id, status="running", store_path=store)

    result = run_safe_restart(tmp_path, "--dry-run", "--reason", "unit-test")

    assert result.returncode == 3
    assert "restart blocked: active work detected" in result.stderr
    event = audit_lines(tmp_path)[-1]
    assert event["event"] == "restart_blocked"
    assert event["status"] == "active_work"
    assert task_id in str(event["details"])


def test_safe_restart_ignores_stale_human_waiting_task_by_default(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Waiting for old approval", store_path=store)["task_id"]
    update_task(task_id, status="running", store_path=store)
    update_task(task_id, status="awaiting_human_decision", store_path=store)
    with sqlite3.connect(store) as con:
        con.execute(
            "UPDATE supervisor_tasks SET updated_at='2026-01-01T00:00:00+00:00' WHERE id=?",
            (task_id,),
        )
        con.commit()

    result = run_safe_restart(tmp_path, "--dry-run", "--reason", "config-only")

    assert result.returncode == 0, result.stderr
    event = audit_lines(tmp_path)[-1]
    assert event["event"] == "restart_skipped"
    assert event["status"] == "dry_run"
    assert event["details"] == "no_active_work"


def test_safe_restart_blocks_recent_human_waiting_task(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Waiting for current approval", store_path=store)["task_id"]
    update_task(task_id, status="running", store_path=store)
    update_task(task_id, status="awaiting_human_decision", store_path=store)

    result = run_safe_restart(tmp_path, "--dry-run", "--reason", "config-only")

    assert result.returncode == 3
    event = audit_lines(tmp_path)[-1]
    assert event["event"] == "restart_blocked"
    assert task_id in str(event["details"])


def test_safe_restart_blocks_running_task_even_when_timestamp_is_old(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Still marked running", store_path=store)["task_id"]
    update_task(task_id, status="running", store_path=store)
    with sqlite3.connect(store) as con:
        con.execute(
            "UPDATE supervisor_tasks SET updated_at='2026-01-01T00:00:00+00:00' WHERE id=?",
            (task_id,),
        )
        con.commit()

    result = run_safe_restart(tmp_path, "--dry-run", "--reason", "config-only")

    assert result.returncode == 3
    event = audit_lines(tmp_path)[-1]
    assert event["event"] == "restart_blocked"
    assert task_id in str(event["details"])


def test_safe_restart_force_allows_active_task_and_records_forced_flag(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Deploy production change", store_path=store)["task_id"]
    update_task(task_id, status="running", store_path=store)

    result = run_safe_restart(tmp_path, "--dry-run", "--force", "--reason", "emergency-test")

    assert result.returncode == 0, result.stderr
    event = audit_lines(tmp_path)[-1]
    assert event["event"] == "restart_skipped"
    assert event["forced"] is True
    assert task_id in str(event["details"])


def test_safe_restart_calls_docker_restart_after_checks(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker_log = tmp_path / "docker.log"
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$FAKE_DOCKER_LOG\"\n"
        "printf '%s\\n' \"$2\"\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = {
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "FAKE_DOCKER_LOG": str(docker_log),
    }
    result = run_safe_restart(tmp_path, "--reason", "unit-test", env=env)

    assert result.returncode == 0, result.stderr
    assert docker_log.read_text(encoding="utf-8") == "restart hermes-agent\n"
    event = audit_lines(tmp_path)[-1]
    assert event["event"] == "restart_completed"
    assert event["status"] == "ok"
