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


def test_startup_context_token_budget_uses_thirty_percent_with_bounds() -> None:
    assert process_context_pack.startup_context_token_budget(100) == 120
    assert process_context_pack.startup_context_token_budget(1000) == 300
    assert process_context_pack.startup_context_token_budget(10000) == 800
    assert process_context_pack.startup_context_token_budget(0) == 300
