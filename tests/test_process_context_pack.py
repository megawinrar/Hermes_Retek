from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_workspace  # noqa: E402
import process_context_pack  # noqa: E402
import rlm_store  # noqa: E402


def route() -> dict[str, object]:
    return {
        "task_level": "L3",
        "task_type": "code_change",
        "risk_level": "high",
        "review_required": True,
        "human_gate_required": False,
        "process_plan": ["bot1", "tester", "bot2"],
        "needs_agents": True,
    }


def skill_context() -> dict[str, object]:
    developer = {"name": "hermes-developer", "path": "skills/hermes-developer/SKILL.md", "tags": ["code"]}
    tester = {"name": "hermes-tester", "path": "skills/hermes-tester/SKILL.md", "tags": ["test"]}
    return {
        "task_tags": ["code", "test"],
        "selected_skills": [developer, tester],
        "roles": {"bot1": [developer], "bot2": [tester], "tester": [tester]},
        "gated_roles": {},
        "runtime_contract": {"load_only_selected_skill_paths": True},
    }


def supplier_route() -> dict[str, object]:
    return {
        "task_level": "L2",
        "task_type": "supplier_price_deadline_analysis",
        "risk_level": "high",
        "review_required": True,
        "human_gate_required": False,
        "process_plan": ["router", "supervisor", "bot1", "bot2"],
        "needs_agents": False,
    }


def test_build_role_context_pack_includes_route_workspace_rlm_and_redacts_sensitive_values(tmp_path: Path) -> None:
    process_id = "proc-context-pack"
    rlm_path = tmp_path / "rlm.db"
    workspace_root = tmp_path / "workspaces"
    token = "tok_" + "A" * 32
    cookie = "cookie_" + "B" * 40

    agent_workspace.create_workspace(process_id, "bot1", workspace_root)
    agent_workspace.set_workspace_status(process_id, "bot1", "running", workspace_root, reason=f"auth.sid={cookie}")
    record = rlm_store.add_record(
        kind="process_summary",
        title="Restart guard lesson",
        summary=f"Use supervisor gate before restart with API_KEY={token} and auth.sid={cookie}",
        content=f"full content API_KEY={token} auth.sid={cookie}",
        tags=["process", "type/code_change", "task/code"],
        process_id=process_id,
        importance=0.9,
        metadata={"artifact_ref": "/opt/data/reports/restart.md", "cookie": f"auth.sid={cookie}"},
        store_path=rlm_path,
    )

    packs = process_context_pack.build_role_context_packs(
        roles=["bot1", "bot2"],
        process_id=process_id,
        task=f"Change restart code with API_KEY={token}",
        acceptance="Need tests and Bot2 review.",
        route=route(),
        skill_context=skill_context(),
        phase="initial",
        prior_verdict={"required_fixes": ["Add rollback evidence."], "risks": ["restart_without_gate"]},
        rlm_store_path=rlm_path,
        rlm_enabled=True,
        workspace_root=str(workspace_root),
        token_budget=180,
    )

    bot1 = packs["bot1"]
    raw = json.dumps(packs, ensure_ascii=False)
    assert bot1["session_strategy"] == process_context_pack.SESSION_STRATEGY
    assert bot1["route"]["task_type"] == "code_change"
    assert bot1["workspace"]["exists"] is True
    assert bot1["workspace"]["status"] == "running"
    assert bot1["required_fixes"] == ["Add rollback evidence."]
    assert "Restart guard lesson" in bot1["rlm_context"]["context"]
    assert bot1["rlm_context"]["records"][0]["id"] == record["id"]
    assert token not in raw
    assert cookie not in raw
    assert "[REDACTED]" in raw

    event = process_context_pack.event_payload(packs)
    event_raw = json.dumps(event, ensure_ascii=False)
    assert event["roles"]["bot1"]["record_ids"] == [record["id"]]
    assert "Restart guard lesson" not in event_raw
    assert "rlm_context" not in event_raw
    assert token not in event_raw
    assert cookie not in event_raw


def test_startup_context_token_budget_uses_large_default_with_bounds() -> None:
    assert process_context_pack.startup_context_token_budget(100) == 120
    assert process_context_pack.startup_context_token_budget(1000) == 500
    assert process_context_pack.startup_context_token_budget(3000) == 1500
    assert process_context_pack.startup_context_token_budget(10000) == 3000
    assert process_context_pack.startup_context_token_budget(0) == 3000


def test_expanded_context_budget_for_complex_retry_and_kontur_tasks() -> None:
    normal = process_context_pack.startup_context_token_budget_for_route(
        3000,
        route={"task_level": "L2", "task_type": "standard_task", "risk_level": "medium"},
        phase="initial",
        task="short status",
    )
    l4 = process_context_pack.startup_context_token_budget_for_route(
        3000,
        route={"task_level": "L4", "task_type": "code_or_deploy_project", "risk_level": "high"},
        phase="initial",
        task="Refactor and deploy",
    )
    retry = process_context_pack.startup_context_token_budget_for_route(
        3000,
        route={"task_level": "L2", "task_type": "standard_task", "risk_level": "medium"},
        phase="human_continue",
        task="Fix Bot2 findings",
    )
    kontur = process_context_pack.startup_context_token_budget_for_route(
        3000,
        route={"task_level": "L2", "task_type": "supplier_price_deadline_analysis", "risk_level": "high"},
        phase="initial",
        task="Continue Kontur export",
    )

    assert normal == 1500
    assert l4 == 2100
    assert retry == 2100
    assert kontur == 2100
    assert process_context_pack.startup_context_token_budget_for_route(
        10000,
        route={"task_level": "L4", "task_type": "code_or_deploy_project", "risk_level": "high"},
    ) == 5000


def test_supplier_context_pack_includes_b2b_public_fallback_policy(tmp_path: Path) -> None:
    pack = process_context_pack.build_role_context_pack(
        role="bot1",
        process_id="proc-b2b-policy",
        task="Спарси https://www.b2b-center.ru/market/ по Р6М5 и Р18 через Puppeteer",
        acceptance="Save JSON results and evidence.",
        route=supplier_route(),
        skill_context={
            "task_tags": ["browser", "supplier"],
            "selected_skills": [{"name": "hermes-browser", "path": "skills/hermes-browser/SKILL.md"}],
            "roles": {"bot1": [{"name": "hermes-browser", "path": "skills/hermes-browser/SKILL.md"}]},
            "gated_roles": {},
            "runtime_contract": {},
        },
        rlm_store_path=tmp_path / "rlm.db",
        workspace_root=str(tmp_path / "workspaces"),
        token_budget=180,
    )

    policy = pack["parsing_policy"]
    assert policy["name"] == "b2b_center"
    assert policy["public_fallback_allowed"] is True
    assert policy["stop_on_failed_login"] is False
    assert policy["login_timeout_seconds"] == 15
    assert "login is optional for public marketplace search; save auth=false and continue if result cards are visible" in policy["rules"]


def test_context_pack_rebuilds_from_process_events_assignments_and_human_decision(tmp_path: Path) -> None:
    process_id = "proc-rebuild"
    events = [
        {
            "event_type": "bot2_verdict",
            "payload": {
                "verdict": {
                    "status": "REQUEST_CHANGES",
                    "summary": "rollback missing",
                    "required_fixes": ["Add rollback evidence.", 42],
                    "risks": ["rollback_not_proven"],
                }
            },
        },
        {
            "event_type": "human_decision",
            "payload": {
                "decision": {
                    "status": "return_to_bot1",
                    "choice": "yes",
                    "meaning": "return to Bot1",
                    "reason": "Bot2 is right",
                    "bot2_session_id": "bot2-session",
                }
            },
        },
    ]
    assignments = [
        {"worker": "router", "phase": "intake", "status": "completed", "output": {"ignored": True}},
        {"worker": "bot1", "phase": "execution", "status": "completed", "created_at": "t1", "output": {"result_chars": 120}},
        {"worker": "tester", "phase": "verification", "status": "completed", "created_at": "t2", "output": {"evidence_chars": 80}},
        {
            "worker": "bot2",
            "phase": "quality_gate",
            "status": "completed",
            "created_at": "t3",
            "output": {
                "verdict": {
                    "status": "REQUEST_CHANGES",
                    "summary": "assignment fallback",
                    "required_fixes": ["fallback fix"],
                    "risks": ["fallback risk"],
                }
            },
        },
    ]

    pack = process_context_pack.build_role_context_pack(
        role="bot1",
        process_id=process_id,
        task="Change restart guard",
        acceptance="Need tests.",
        route=route(),
        skill_context=skill_context(),
        phase="human_continue",
        events=events,
        assignments=assignments,
        previous_answer="Previous Bot1 answer",
        rlm_store_path=tmp_path / "missing-rlm.db",
        workspace_root=str(tmp_path / "workspaces"),
        token_budget=120,
    )

    assert pack["required_fixes"] == ["Add rollback evidence.", "42"]
    assert pack["known_risks"] == ["rollback_not_proven"]
    assert pack["human_decision"]["choice"] == "yes"
    assert pack["human_decision"]["reason"] == "Bot2 is right"
    assert pack["previous_answer_preview"] == "Previous Bot1 answer"
    assert [item["worker"] for item in pack["previous_attempts"]] == ["bot1", "tester", "bot2"]
    assert pack["previous_attempts"][-1]["verdict"]["summary"] == "assignment fallback"


def test_context_pack_falls_back_to_assignment_verdict_without_event(tmp_path: Path) -> None:
    pack = process_context_pack.build_role_context_pack(
        role="bot1",
        process_id="proc-assignment-fallback",
        task="Fix code",
        acceptance="Need review.",
        route=route(),
        skill_context=skill_context(),
        assignments=[
            {
                "worker": "bot2",
                "phase": "quality_gate",
                "status": "completed",
                "output": {
                    "verdict": {
                        "required_fixes": ["Use assignment verdict."],
                        "risks": ["assignment_risk"],
                    }
                },
            }
        ],
        workspace_root=str(tmp_path / "workspaces"),
    )

    assert pack["required_fixes"] == ["Use assignment verdict."]
    assert pack["known_risks"] == ["assignment_risk"]


def test_context_pack_handles_workspace_and_rlm_failures_without_secret_leaks(monkeypatch, tmp_path: Path) -> None:
    token = "tok_" + "D" * 32

    def fail_build_context_pack(**_kwargs: object) -> dict[str, object]:
        raise RuntimeError(f"cannot open RLM Authorization: Bearer {token}")

    monkeypatch.setattr(process_context_pack.rlm_store, "build_context_pack", fail_build_context_pack)

    pack = process_context_pack.build_role_context_pack(
        role="bot1",
        process_id="bad/process-id",
        task="Fix code",
        acceptance="Need no leaks.",
        route=route(),
        skill_context=skill_context(),
        rlm_enabled=True,
        rlm_store_path=tmp_path / "rlm.db",
    )
    raw = json.dumps(pack, ensure_ascii=False)

    assert pack["workspace"]["status"] == "unavailable"
    assert pack["rlm_context"]["status"] == "unavailable"
    assert token not in raw
    assert "[REDACTED]" in raw


def test_merge_rlm_packs_deduplicates_and_respects_budget() -> None:
    pack = process_context_pack._merge_rlm_packs(
        [
            {
                "context": "[1] kind A: short\n[2] kind B: " + ("x" * 200),
                "records": [{"id": 1}, {"id": 2}],
            },
            {
                "context": "[1] duplicate should skip\n\n[3] kind C: short",
                "records": [{"id": 1}, {"id": 99}, {"id": 3}],
            },
        ],
        token_budget=6,
    )

    assert [record["id"] for record in pack["records"]] == [1]
    assert "[1] kind A: short" in pack["context"]
    assert "kind B" not in pack["context"]
    assert "duplicate should skip" not in pack["context"]
