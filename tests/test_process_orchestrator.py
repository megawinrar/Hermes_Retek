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
        "route_audit_mode": "auto",
        "no_route_audit_cache": False,
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
    assert {item["worker"] for item in details["assignments"]} == {"router", "skill_index", "supervisor", "bot1"}
    assert details["summary"]["status"] == "approved"
    assert details["summary"]["task_level"] == "L1"
    assert details["summary"]["skills"]["selected"] == ["hermes-developer"]
    assert details["summary"]["skills"]["roles"]["bot1"] == ["hermes-developer"]
    assert details["summary"]["bot2"]["required"] is False
    assert details["summary"]["waiting_on"] == ""
    assert details["summary"]["supervisor_available"] is False
    assert details["timeline"]
    bot1_assignment = [item for item in details["assignments"] if item["worker"] == "bot1"][-1]
    assert bot1_assignment["output"]["skills"]["skills"][0]["path"] == "skills/hermes-developer/SKILL.md"


def test_process_route_cache_returns_isolated_copies() -> None:
    process_orchestrator.clear_runtime_caches()

    first = process_orchestrator.classify_task("status")
    first["task_level"] = "L4"
    second = process_orchestrator.classify_task("status")

    assert second["task_level"] == "L0"
    assert process_orchestrator.runtime_cache_stats()["route_entries"] >= 1


def test_adaptive_token_budget_scales_by_task_level(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_BOT1_MAX_TOKENS", raising=False)
    monkeypatch.delenv("HERMES_BOT2_VERDICT_MAX_TOKENS", raising=False)
    monkeypatch.delenv("HERMES_ADAPTIVE_TOKEN_BUDGET", raising=False)

    assert process_orchestrator.token_budget_for_role(
        1400,
        role="bot1",
        route={"task_level": "L1", "risk_level": "low"},
    ) == 512
    assert process_orchestrator.token_budget_for_role(
        1400,
        role="bot1",
        route={"task_level": "L3", "risk_level": "high"},
    ) == 1400
    assert process_orchestrator.token_budget_for_role(
        1400,
        role="bot1",
        route={"task_level": "L4", "human_gate_required": True},
    ) == 1400
    assert process_orchestrator.token_budget_for_role(
        1400,
        role="bot2_verdict",
        route={"task_level": "L4", "human_gate_required": True},
    ) == 900


def test_token_budget_env_override_beats_adaptive_policy(monkeypatch) -> None:
    monkeypatch.setenv("HERMES_BOT1_MAX_TOKENS", "1200")

    assert process_orchestrator.token_budget_for_role(
        1400,
        role="bot1",
        route={"task_level": "L1", "risk_level": "low"},
    ) == 1200


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
    assert "skill_context_selected" in event_types
    assert "bot2_route_audit" in workers
    assert details["summary"]["route"]["classification_audit"]["applied"]
    assert "hermes-devops" in details["summary"]["skills"]["gated"]


def test_live_route_audit_auto_skips_low_risk_l1(monkeypatch, tmp_path: Path) -> None:
    import dual_bot_lab

    monkeypatch.setattr(dual_bot_lab, "bothub_config", lambda: (_ for _ in ()).throw(AssertionError("Bot#2 audit not expected")))
    monkeypatch.setattr(dual_bot_lab, "call_chat", lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Bot#2 audit not expected")))

    args = args_for_process(
        tmp_path,
        task="rewrite short hello",
        acceptance="short answer",
        live_route_audit=True,
    )
    payload = process_orchestrator.run_process(args)

    assert payload["status"] == "approved"
    audit = payload["route"]["classification_audit"]
    assert audit["status"] == "SKIPPED_LOW_RISK_FAST_PATH"
    assert audit["source"] == "supervisor_route_audit_policy"
    assert payload["performance"]["route_audit"]["skipped"] is True

    details = process_orchestrator.process_details(
        payload["process_id"],
        store_path=args.process_store,
        supervisor_store_path=args.supervisor_store,
    )
    workers = {assignment["worker"] for assignment in details["assignments"]}
    assert "route_audit_policy" in workers
    assert "bot2_route_audit" not in workers
    assert details["summary"]["performance"]["route_audit"]["skipped"] is True


def test_live_route_audit_always_bypasses_low_risk_fast_path(monkeypatch, tmp_path: Path) -> None:
    import dual_bot_lab

    calls = 0

    def fake_call_chat(**_kwargs):
        nonlocal calls
        calls += 1
        return (
            json.dumps(
                {
                    "status": "CONFIRM",
                    "recommended_level": "L1",
                    "risk_level": "low",
                    "review_required": False,
                    "human_gate_required": False,
                    "summary": "Low-risk route confirmed by forced Bot#2 audit.",
                    "signals": ["forced_audit"],
                }
            ),
            {"usage": {"total_tokens": 8}},
        )

    monkeypatch.setattr(dual_bot_lab, "bothub_config", lambda: {"base_url": "https://example.test/v1", "api_key": "test"})
    monkeypatch.setattr(dual_bot_lab, "call_chat", fake_call_chat)

    args = args_for_process(
        tmp_path,
        task="rewrite short hello",
        acceptance="short answer",
        live_route_audit=True,
        route_audit_mode="always",
    )
    payload = process_orchestrator.run_process(args)

    assert calls == 1
    assert payload["status"] == "approved"
    assert payload["route"]["classification_audit"]["source"] == "bot2_live_route_audit"
    assert payload["performance"]["route_audit"]["skipped"] is False


def test_live_route_audit_runs_for_high_risk_route_and_records_latency(monkeypatch, tmp_path: Path) -> None:
    import dual_bot_lab

    calls: list[list[dict[str, str]]] = []

    def fake_call_chat(**kwargs):
        calls.append(kwargs["messages"])
        return (
            json.dumps(
                {
                    "status": "CONFIRM",
                    "recommended_level": "L4",
                    "risk_level": "high",
                    "review_required": True,
                    "human_gate_required": True,
                    "summary": "High-risk deploy route confirmed.",
                    "signals": ["deploy", "production"],
                }
            ),
            {"usage": {"total_tokens": 21}},
        )

    monkeypatch.setattr(dual_bot_lab, "bothub_config", lambda: {"base_url": "https://example.test/v1", "api_key": "test"})
    monkeypatch.setattr(dual_bot_lab, "call_chat", fake_call_chat)

    args = args_for_process(
        tmp_path,
        task="Change python code and deploy to production server",
        live_route_audit=True,
        notification_dry_run=True,
    )
    payload = process_orchestrator.run_process(args)

    assert len(calls) == 1
    audit = payload["route"]["classification_audit"]
    assert audit["source"] == "bot2_live_route_audit"
    assert audit["status"] == "CONFIRM"
    assert audit["raw"]["usage"]["total_tokens"] == 21
    assert "latency_ms" in audit["raw"]
    assert payload["performance"]["route_audit"]["skipped"] is False
    assert payload["performance"]["route_audit"]["cache_hit"] is False
    assert payload["performance"]["route_audit"]["model"] == "bot2-model"

    details = process_orchestrator.process_details(
        payload["process_id"],
        store_path=args.process_store,
        supervisor_store_path=args.supervisor_store,
    )
    workers = {assignment["worker"] for assignment in details["assignments"]}
    assert "bot2_route_audit" in workers
    assert details["summary"]["performance"]["route_audit"]["status"] == "CONFIRM"


def test_process_performance_aggregates_llm_http_timing() -> None:
    performance = process_orchestrator.build_process_performance(
        duration_ms=1234,
        route_audit={},
        verdict={
            "review_cycles": [
                {
                    "latency_ms": {"bot1": 1000, "bot2": 2000, "bot2_repair": 0},
                    "http_timing_ms": {
                        "bot1": {"total": 900, "time_to_headers": 880, "read_body": 20},
                        "bot2": {"total": 1900, "time_to_headers": 1880, "read_body": 20},
                        "bot2_repair": {},
                    },
                    "completion_budget": {
                        "bot1": {"hit_cap": True},
                        "bot2": {"hit_cap": False},
                        "bot2_repair": {},
                    },
                }
            ]
        },
    )

    assert performance["live_review"]["latency_ms"] == 3000
    assert performance["live_review"]["llm_call_count"] == 2
    assert performance["live_review"]["http_timing_ms"] == {
        "request_count": 2,
        "total": 2800,
        "time_to_headers": 2760,
        "read_body": 40,
    }
    assert performance["live_review"]["completion_budget"] == {
        "cap_hit_count": 1,
        "cap_hit_roles": ["bot1"],
    }


def test_live_route_audit_cache_reuses_previous_bot2_result(monkeypatch, tmp_path: Path) -> None:
    import dual_bot_lab

    process_orchestrator.clear_runtime_caches()
    calls = 0

    def fake_call_chat(**_kwargs):
        nonlocal calls
        calls += 1
        return (
            json.dumps(
                {
                    "status": "CONFIRM",
                    "recommended_level": "L3",
                    "risk_level": "high",
                    "review_required": True,
                    "human_gate_required": False,
                    "summary": "Migration planning route confirmed.",
                    "signals": ["database", "migration"],
                }
            ),
            {"usage": {"total_tokens": 34}},
        )

    monkeypatch.setattr(dual_bot_lab, "bothub_config", lambda: {"base_url": "https://example.test/v1", "api_key": "test"})
    monkeypatch.setattr(dual_bot_lab, "call_chat", fake_call_chat)

    first_args = args_for_process(
        tmp_path,
        task="Plan database migration for customer schema with rollback",
        live_route_audit=True,
    )
    second_args = args_for_process(
        tmp_path,
        task="Plan database migration for customer schema with rollback",
        live_route_audit=True,
    )

    first = process_orchestrator.run_process(first_args)
    second = process_orchestrator.run_process(second_args)

    assert calls == 1
    assert first["route"]["classification_audit"]["source"] == "bot2_live_route_audit"
    assert second["route"]["classification_audit"]["source"] == "bot2_live_route_audit_cache"
    assert second["route"]["classification_audit"]["raw"]["cache_hit"] is True
    assert second["route"]["classification_audit"]["raw"]["latency_ms"] == 0
    assert "original_latency_ms" in second["route"]["classification_audit"]["raw"]
    assert second["performance"]["route_audit"]["cache_hit"] is True
    assert second["performance"]["route_audit"]["latency_ms"] == 0
    assert process_orchestrator.runtime_cache_stats()["route_audit_entries"] >= 1

    details = process_orchestrator.process_details(
        second["process_id"],
        store_path=second_args.process_store,
        supervisor_store_path=second_args.supervisor_store,
    )
    workers = {assignment["worker"] for assignment in details["assignments"]}
    assert "bot2_route_audit_cache" in workers


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
    assert {item["worker"] for item in details["assignments"]} == {"router", "skill_index", "supervisor"}
    assert details["summary"]["skills"]["selected"] == []


def test_process_records_skill_context_for_l4_without_enabling_gated_devops(tmp_path: Path) -> None:
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
        "--notification-dry-run",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "awaiting_human_decision"

    details = process_orchestrator.process_details(
        payload["process_id"],
        store_path=process_store,
        supervisor_store_path=supervisor_store,
    )
    summary = details["summary"]
    assert summary["skills"]["selection_policy"] == "lazy_by_task_level_role_and_tags"
    assert "github-code-review" in summary["skills"]["selected"]
    assert summary["skills"]["gated_roles"]["devops"] == ["hermes-devops", "github-pr-workflow"]
    assert "github-pr-workflow" in summary["skills"]["gated_roles"]["supervisor"]
    assert summary["skills"]["runtime_contract"]["approval_required_skills_are_gated"] is True
    skill_event = [event for event in details["events"] if event["event_type"] == "skill_context_selected"][-1]
    assert skill_event["payload"]["task_type"] == "git_write_or_deploy"


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
    assert notification["decision_commands"]["yes"].endswith(f"decide {payload['process_id']} --choice yes --reason \"...\"")
    assert notification["decision_commands"]["no"].endswith(f"decide {payload['process_id']} --choice no --reason \"...\"")


def test_process_decide_yes_returns_bot1_to_fixes(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    verdict = {
        "status": "REQUEST_CHANGES",
        "summary": "Bot#2 found missing rollback evidence.",
        "approved_action": "needs_human",
        "evidence_checked": ["bot1"],
        "risks": ["rollback_not_proven"],
        "required_fixes": ["Add rollback evidence and rerun tests."],
        "confidence": 0.72,
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
        "Change python code and deploy to production server",
        "--bot2-verdict-json",
        json.dumps(verdict),
        "--notification-dry-run",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "awaiting_human_decision"

    decided = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "decide",
        payload["process_id"],
        "--choice",
        "yes",
        "--reason",
        "Bot2 is right",
    )
    assert decided.returncode == 0, decided.stderr
    decision = json.loads(decided.stdout)
    assert decision["status"] == "return_to_bot1"
    assert decision["decision"]["choice"] == "yes"
    assert decision["next_action"]["action"] == "return_to_bot1_with_bot2_fixes"
    assert decision["next_action"]["target_worker"] == "bot1"
    assert decision["next_action"]["required_fixes"] == ["Add rollback evidence and rerun tests."]

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
    assert shown.returncode == 0, shown.stderr
    details = json.loads(shown.stdout)
    assert details["summary"]["status"] == "return_to_bot1"
    assert details["summary"]["current_phase"] == "bot1_revision"
    assert details["summary"]["human_decision"]["choice"] == "yes"
    assert details["summary"]["next_action"]["action"] == "return_to_bot1_with_bot2_fixes"
    assert {"human_decision", "process_next_action"} <= {event["event_type"] for event in details["events"]}
    pending_bot1_revision = [
        item for item in details["assignments"] if item["worker"] == "bot1" and item["phase"] == "revision"
    ]
    assert pending_bot1_revision
    assert pending_bot1_revision[-1]["status"] == "pending"


def test_process_continue_after_yes_runs_bot1_revision_and_bot2_review(tmp_path: Path) -> None:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    verdict = {
        "status": "REQUEST_CHANGES",
        "summary": "Bot#2 found missing rollback evidence.",
        "approved_action": "needs_human",
        "evidence_checked": ["bot1"],
        "risks": ["rollback_not_proven"],
        "required_fixes": ["Add rollback evidence and rerun tests."],
        "confidence": 0.72,
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
        "Change python code and deploy to production server",
        "--bot2-verdict-json",
        json.dumps(verdict),
        "--notification-dry-run",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    decided = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "decide",
        payload["process_id"],
        "--choice",
        "yes",
        "--reason",
        "Bot2 is right",
    )
    assert decided.returncode == 0, decided.stderr

    continued = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "continue",
        payload["process_id"],
        "--mode",
        "dry",
        "--notification-dry-run",
    )
    assert continued.returncode == 0, continued.stderr
    continuation = json.loads(continued.stdout)
    assert continuation["status"] == "approved"
    assert continuation["mode"] == "dry"
    assert continuation["bot2_session_id"].endswith("-bot2-continue-dry")
    assert continuation["bot2_verdict"]["status"] == "APPROVE_WITH_EVIDENCE"
    assert continuation["next_action"]["action"] == "completed_after_bot1_revision"

    details = process_orchestrator.process_details(
        payload["process_id"],
        store_path=process_store,
        supervisor_store_path=supervisor_store,
    )
    assert details["summary"]["status"] == "approved"
    assert details["summary"]["current_phase"] == "approved"
    assert details["summary"]["next_action"]["action"] == "completed_after_bot1_revision"
    revision_assignments = [
        item for item in details["assignments"] if item["worker"] == "bot1" and item["phase"] == "revision"
    ]
    assert [item["status"] for item in revision_assignments][-2:] == ["running", "completed"]
    assert revision_assignments[-1]["output"]["mode"] == "dry"
    assert "bot1_revision" in {event["event_type"] for event in details["events"]}

    transcript = process_orchestrator.process_transcript(
        payload["process_id"],
        store_path=process_store,
        supervisor_store_path=supervisor_store,
    )
    assert transcript["status"] == "approved"
    assert transcript["review_cycles"][-1]["human_continue"] is True
    assert transcript["fix_closure_checklist"][0]["required_fix"] == "Add rollback evidence and rerun tests."


def test_process_decide_no_accepts_bot1_user_override(tmp_path: Path) -> None:
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
        "--bot2-status",
        "REJECT",
        "--notification-dry-run",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    decided = run_cli(
        sys.executable,
        str(SCRIPTS / "process_orchestrator.py"),
        "--process-store",
        str(process_store),
        "--supervisor-store",
        str(supervisor_store),
        "decide",
        payload["process_id"],
        "--choice",
        "no",
        "--reason",
        "User accepts Bot1",
    )
    assert decided.returncode == 0, decided.stderr
    decision = json.loads(decided.stdout)
    assert decision["status"] == "accepted_by_user_override"
    assert decision["decision"]["choice"] == "no"
    assert decision["next_action"]["action"] == "accept_bot1_user_override"
    assert decision["next_action"]["target_worker"] == "supervisor"
    assert decision["next_action"]["devops_allowed_after_override"] is True

    details = process_orchestrator.process_details(
        payload["process_id"],
        store_path=process_store,
        supervisor_store_path=supervisor_store,
    )
    assert details["summary"]["status"] == "accepted_by_user_override"
    assert details["summary"]["current_phase"] == "final_decision"
    assert details["summary"]["human_decision"]["choice"] == "no"
    assert details["summary"]["next_action"]["action"] == "accept_bot1_user_override"


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
