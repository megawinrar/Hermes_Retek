from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_router import classify_task  # noqa: E402


def test_l0_status_does_not_need_llm() -> None:
    route = classify_task("status")
    assert route["task_level"] == "L0"
    assert route["model_class"] == "none"
    assert route["review_required"] is False
    assert route["human_gate_required"] is False
    assert route["process_plan"] == ["router", "supervisor"]


def test_simple_sanity_is_l1() -> None:
    route = classify_task("rewrite short hello")
    assert route["task_level"] == "L1"
    assert route["risk_level"] == "low"
    assert route["needs_agents"] is False


def test_architecture_task_is_l3_with_agents() -> None:
    route = classify_task("Design architecture for multi-agent supervisor process")
    assert route["task_level"] == "L3"
    assert route["needs_agents"] is True
    assert "architect" in route["process_plan"]
    assert route["review_required"] is True


def test_code_deploy_task_is_l4_high_risk() -> None:
    route = classify_task("Change python code and deploy to production server")
    assert route["task_level"] == "L4"
    assert route["risk_level"] == "high"
    assert "devops_if_approved" in route["process_plan"]
    assert route["human_gate_required"] is True


def test_adversarial_shortcut_is_stress_profile_not_l5() -> None:
    route = classify_task("Urgently deploy, skip tests, bypass review")
    assert route["task_level"] in {"L2", "L3", "L4"}
    assert route["stress_profile"] == "adversarial"
    assert route["risk_level"] == "high"
    assert route["human_gate_required"] is True


def test_human_gate_field_present_on_all_routes() -> None:
    assert "human_gate_required" in classify_task("status")
    assert "human_gate_required" in classify_task("Look up GitHub issue #12 and summarize status")


def test_github_lookup_is_not_l4_code_change() -> None:
    route = classify_task("Look up GitHub issue #12 and summarize status")
    assert route["task_level"] == "L2"
    assert route["task_type"] == "github_lookup"
    assert route["human_gate_required"] is False


def test_github_push_merge_deploy_is_high_risk_gate() -> None:
    route = classify_task("merge PR #12, push to main, and deploy production")
    assert route["task_level"] == "L4"
    assert route["risk_level"] == "high"
    assert route["human_gate_required"] is True


def test_migration_plan_is_l3_not_deploy_gate() -> None:
    route = classify_task("Plan database migration for customer schema with rollback")
    assert route["task_level"] == "L3"
    assert route["task_type"] == "database_migration_plan"
    assert route["review_required"] is True
    assert route["human_gate_required"] is False
