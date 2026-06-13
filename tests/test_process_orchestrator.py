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
    assert details["summary"]["status"] == "approved"
    assert details["summary"]["task_level"] == "L1"
    assert details["summary"]["bot2"]["required"] is False
    assert details["summary"]["waiting_on"] == ""
    assert details["summary"]["supervisor_available"] is False
    assert details["timeline"]


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


def test_process_transcript_shows_bot1_bot2_and_human_gate(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    bot1_result = "Bot#1 proposal: change supplier import, add tests, wait for deploy approval."
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "run",
        "--task",
        "Change CRM supplier import and deploy to production server",
        "--acceptance",
        "Need Bot#1/Bot#2 transcript",
        "--bot1-result",
        bot1_result,
        "--evidence",
        "tests=not_run; rollback=restore previous import script",
        "--bot2-status",
        "REJECT",
        "--notification-dry-run",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    transcript = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "transcript",
        payload["process_id"],
    )
    assert transcript.returncode == 0, transcript.stderr
    data = json.loads(transcript.stdout)
    assert data["status"] == "awaiting_human_decision"
    by_actor = {item["actor"]: item for item in data["conversation"]}
    assert by_actor["bot1"]["content"] == bot1_result
    assert by_actor["tester"]["content"] == "tests=not_run; rollback=restore previous import script"
    assert by_actor["bot2"]["status"] == "REJECT"
    assert by_actor["bot2"]["content"]["summary"] == "Dry Bot#2 verdict: REJECT"
    assert data["human_gate"]["required"] is True
    assert data["human_gate"]["status"] == "awaiting_decision"
    assert "Версия Bot#1" in data["human_gate"]["message"]
    assert data["human_gate"]["delivery"]["mode"] == "dry_run"
    assert {run["role"] for run in data["audit"]["role_runs"]} == {"bot1", "tester", "bot2"}


def test_process_transcript_accepts_explicit_bot2_verdict_json(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    bot1_result = "Bot#1 action: changed scripts/task_router.py and tests/test_task_router.py."
    verdict = {
        "status": "APPROVE_WITH_EVIDENCE",
        "summary": "Bot#2 reviewed the router diff and focused tests; supplier task type is now explicit.",
        "approved_action": "execute",
        "evidence_checked": ["pytest tests/test_task_router.py -q", "manual route smoke"],
        "risks": ["live LLM/API not exercised until keys are rotated"],
        "required_fixes": [],
        "confidence": 0.88,
    }
    result = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "run",
        "--task",
        "Change task_router.py Python code and add pytest coverage",
        "--acceptance",
        "Show concrete Bot#1 and Bot#2 actions in transcript",
        "--bot1-result",
        bot1_result,
        "--evidence",
        "tests/test_task_router.py passed",
        "--bot2-verdict-json",
        json.dumps(verdict),
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "approved"

    transcript = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "transcript",
        payload["process_id"],
    )
    assert transcript.returncode == 0, transcript.stderr
    data = json.loads(transcript.stdout)
    by_actor = {item["actor"]: item for item in data["conversation"]}
    assert by_actor["bot1"]["content"] == bot1_result
    assert by_actor["bot2"]["status"] == "APPROVE_WITH_EVIDENCE"
    assert by_actor["bot2"]["content"]["summary"] == verdict["summary"]
    assert by_actor["bot2"]["content"]["evidence_checked"] == verdict["evidence_checked"]


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
        "--supervisor-store",
        str(supervisor_store),
        "show",
        payload["process_id"],
    )
    details = json.loads(shown.stdout)
    assert details["summary"]["status"] == "awaiting_human_decision"
    assert details["summary"]["waiting_on"] == "human"
    assert details["summary"]["human_decision"]["required"] is True
    assert details["summary"]["human_decision"]["status"] == "awaiting_decision"
    assert "Bot#2" in details["summary"]["human_decision"]["yes_meaning"]
    assert details["summary"]["notification"]["mode"] == "record_only"
    assert details["summary"]["supervisor_available"] is True
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
    assert details["summary"]["human_decision"]["required"] is True
    assert details["summary"]["notification"]["mode"] == "dry_run"
    notification_events = [event for event in details["events"] if event["event_type"] == "human_notification"]
    assert len(notification_events) == 1
    assert notification_events[0]["payload"]["delivery"]["mode"] == "dry_run"
    assert secret not in json.dumps(details["summary"], ensure_ascii=False)
    assert secret not in json.dumps(details["timeline"], ensure_ascii=False)


def test_process_events_command_outputs_redacted_jsonl(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    secret = "tok_" + "B" * 32
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
    payload = json.loads(result.stdout)

    events = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "events",
        payload["process_id"],
    )
    assert events.returncode == 0, events.stderr
    assert secret not in events.stdout
    lines = [json.loads(line) for line in events.stdout.splitlines() if line.strip()]
    assert {line["event_type"] for line in lines} >= {"routed", "bot2_verdict", "human_notification"}
