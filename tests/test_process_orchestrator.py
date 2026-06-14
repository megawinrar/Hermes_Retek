from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import process_orchestrator  # noqa: E402


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=False)


def args_for_process(tmp_path: Path, **overrides: object) -> object:
    values: dict[str, object] = {
        "process_store": tmp_path / "process.db",
        "supervisor_store": tmp_path / "supervisor.db",
        "task": "Change python code and add tests",
        "acceptance": "Need tests and Bot2 review",
        "bot1_result": "",
        "evidence": "",
        "bot2_status": "APPROVE",
        "bot2_verdict_json": "",
        "bot2_route_audit_json": "",
        "live_route_audit": False,
        "live_dual": False,
        "bot1_model": "bot1-model",
        "bot2_model": "bot2-model",
        "timeout": 10,
        "max_tokens": 100,
        "notify_telegram": False,
        "notification_dry_run": True,
    }
    values.update(overrides)
    return type("Args", (), values)()


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


def test_bot2_route_audit_raises_l1_to_human_gate(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    audit = {
        "status": "REQUIRE_HUMAN_GATE",
        "recommended_level": "L4",
        "risk_level": "high",
        "review_required": True,
        "human_gate_required": True,
        "summary": "Bot#2 saw deploy/write risk hidden in the user context.",
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
        "rewrite short hello",
        "--acceptance",
        "short answer",
        "--bot2-route-audit-json",
        json.dumps(audit),
        "--bot2-status",
        "APPROVE",
        "--notification-dry-run",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["route"]["task_level"] == "L4"
    assert payload["route"]["risk_level"] == "high"
    assert payload["route"]["human_gate_required"] is True
    assert payload["status"] == "awaiting_human_decision"
    assert payload["bot2_verdict"]["status"] == "NEEDS_HUMAN"

    details = process_orchestrator.process_details(
        payload["process_id"],
        store_path=process_store,
        supervisor_store_path=supervisor_store,
    )
    event_types = {event["event_type"] for event in details["events"]}
    workers = {assignment["worker"] for assignment in details["assignments"]}
    assert "classification_audit" in event_types
    assert "bot2_route_audit" in workers
    assert details["summary"]["route"]["classification_audit"]["applied"]


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


def test_process_live_dual_repairs_invalid_bot2_json_in_main_orchestrator(monkeypatch, tmp_path: Path) -> None:
    import dual_bot_lab

    calls: list[list[dict[str, str]]] = []
    speakers: list[str] = []

    def fake_call_chat(**kwargs):
        messages = kwargs["messages"]
        calls.append(messages)
        if len(calls) == 1:
            return "Bot#1 result", {"usage": {"total_tokens": 10}}
        if len(calls) == 2:
            return "Bot#2 prose without JSON", {"usage": {"total_tokens": 12}}
        return (
            '{"status":"APPROVE_WITH_EVIDENCE","approved_action":"execute",'
            '"summary":"repair ok","evidence_checked":["bot1"],'
            '"risks":[],"required_fixes":[],"confidence":0.9}',
            {"usage": {"total_tokens": 14}},
        )

    def fake_add_message(_run_id, speaker, *_args, **_kwargs):
        speakers.append(speaker)

    monkeypatch.setattr(dual_bot_lab, "bothub_config", lambda: {"base_url": "https://example.test/v1", "api_key": "test"})
    monkeypatch.setattr(dual_bot_lab, "run_id", lambda: "dual-process-repair")
    monkeypatch.setattr(dual_bot_lab, "add_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "call_chat", fake_call_chat)
    monkeypatch.setattr(dual_bot_lab, "add_message", fake_add_message)
    monkeypatch.setattr(dual_bot_lab, "update_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(dual_bot_lab, "write_report", lambda **kwargs: tmp_path / "report.md")

    args = args_for_process(tmp_path, live_dual=True)
    payload = process_orchestrator.run_process(args)

    assert payload["status"] == "approved"
    assert payload["bot2_verdict"]["status"] == "APPROVE_WITH_EVIDENCE"
    assert payload["bot2_verdict"]["repair_attempted"] is True
    assert payload["bot2_verdict"]["repair_status"] == "repaired"
    assert speakers == ["Bot#1", "Bot#2-1", "Bot#2-repair-1"]
    assert "Return ONLY valid JSON matching this schema" in calls[2][1]["content"]

    details = process_orchestrator.process_details(
        payload["process_id"],
        store_path=args.process_store,
        supervisor_store_path=args.supervisor_store,
    )
    assert details["summary"]["bot2"]["repair_attempted"] is True
    assert details["summary"]["bot2"]["repair_status"] == "repaired"
    assert "bot2_json_repair" in {event["event_type"] for event in details["events"]}


def test_process_escalates_when_repair_loop_exhausts_max_cycles(monkeypatch, tmp_path: Path) -> None:
    verdict = {
        "status": "REQUEST_CHANGES",
        "summary": "max repair cycles exhausted",
        "approved_action": "needs_human",
        "evidence_checked": ["bot1"],
        "risks": ["max_review_cycles_reached"],
        "required_fixes": ["fix still open"],
        "confidence": 0.6,
        "loop_status": "max_review_cycles_reached",
        "review_cycles": [
            {
                "round": 3,
                "bot1_self_check": True,
                "bot2_status": "REQUEST_CHANGES",
                "fix_closure_checklist": [{"required_fix": "fix still open", "status": "claimed_closed_by_bot1_self_check"}],
            }
        ],
        "fix_closure_checklist": [{"required_fix": "fix still open", "status": "claimed_closed_by_bot1_self_check"}],
    }
    monkeypatch.setattr(
        process_orchestrator,
        "live_dual_result",
        lambda *args, **kwargs: (
            "Bot#1 final self-check answer",
            "dual-max-cycles",
            verdict,
            str(tmp_path / "report.md"),
        ),
    )

    args = args_for_process(tmp_path, live_dual=True, notification_dry_run=True)
    payload = process_orchestrator.run_process(args)

    assert payload["status"] == "awaiting_human_decision"
    assert payload["bot2_verdict"]["status"] == "REQUEST_CHANGES"
    assert "Версия Bot#1" in payload["human_message"]
    assert "Версия Bot#2" in payload["human_message"]
    assert payload["human_notification"]["kind"] == "human_decision_required"

    details = process_orchestrator.process_details(
        payload["process_id"],
        store_path=args.process_store,
        supervisor_store_path=args.supervisor_store,
    )
    assert details["summary"]["waiting_on"] == "human"
    assert details["summary"]["human_decision"]["required"] is True
    assert details["summary"]["human_decision"]["status"] == "awaiting_decision"
    events = {event["event_type"] for event in details["events"]}
    assert "human_notification" in events
    assert "repair_loop_exhausted" in events


def test_process_transcript_includes_bot1_self_check_fix_closure_checklist(monkeypatch, tmp_path: Path) -> None:
    checklist = [{"required_fix": "Use inverse normalization", "status": "claimed_closed_by_bot1_self_check"}]
    verdict = {
        "status": "APPROVE_WITH_EVIDENCE",
        "summary": "self-check closed fixes",
        "approved_action": "execute",
        "evidence_checked": ["self-check"],
        "risks": [],
        "required_fixes": [],
        "confidence": 0.88,
        "review_cycles": [
            {
                "round": 2,
                "bot1_self_check": True,
                "bot2_status": "APPROVE_WITH_EVIDENCE",
                "fix_closure_checklist": checklist,
            }
        ],
        "fix_closure_checklist": checklist,
    }
    bot1_answer = (
        "## Bot#1 Self-Checked Answer\n"
        "Fixed scoring.\n"
        "## Self-Consistency Checklist\n"
        "- [x] Use inverse normalization\n"
        "## Evidence\n"
        "Self-check passed."
    )
    monkeypatch.setattr(
        process_orchestrator,
        "live_dual_result",
        lambda *args, **kwargs: (bot1_answer, "dual-self-check", verdict, str(tmp_path / "report.md")),
    )

    args = args_for_process(tmp_path, live_dual=True)
    payload = process_orchestrator.run_process(args)
    transcript = process_orchestrator.process_transcript(
        payload["process_id"],
        store_path=args.process_store,
        supervisor_store_path=args.supervisor_store,
    )

    by_actor = {item["actor"]: item for item in transcript["conversation"]}
    assert "## Bot#1 Self-Checked Answer" in by_actor["bot1"]["content"]
    assert "- [x] Use inverse normalization" in by_actor["bot1"]["content"]
    assert by_actor["bot2"]["status"] == "APPROVE_WITH_EVIDENCE"
    assert by_actor["bot1_self_check"]["content"]["fix_closure_checklist"] == checklist
    assert transcript["fix_closure_checklist"] == checklist
    assert transcript["review_cycles"][0]["bot1_self_check"] is True


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
