from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from skill_index import cache_stats, clear_caches, load_manifest, select_skill_context, select_skills  # noqa: E402


def names(items: list[dict[str, object]]) -> set[str]:
    return {str(item["name"]) for item in items}


def test_skill_manifest_validates_and_paths_exist() -> None:
    manifest = load_manifest()
    assert manifest["version"] == 1
    assert manifest["task_type_tags"]["code_change"] == ["code", "tdd", "implementation", "testing", "review"]
    assert "browser" in manifest["task_type_tags"]["supplier_price_deadline_analysis"]
    for item in manifest["skills"]:
        assert (ROOT / item["path"]).exists()


def test_l0_and_l1_do_not_load_heavy_devops_or_github_skills() -> None:
    manifest = load_manifest()
    assert select_skills(manifest, level="L0") == []
    l1 = select_skills(manifest, level="L1")
    assert names(l1) == {"hermes-developer"}
    assert all(not item.get("gateway_required") for item in l1)


def test_l4_devops_requires_explicit_approval_inclusion() -> None:
    manifest = load_manifest()
    devops_without_approval = select_skills(manifest, level="L4", role="devops")
    assert devops_without_approval == []
    devops_with_approval = select_skills(
        manifest,
        level="L4",
        role="devops",
        include_approval_required=True,
    )
    assert names(devops_with_approval) == {"hermes-devops", "github-pr-workflow"}
    assert all(item.get("gateway_required") for item in devops_with_approval)


def test_skill_index_cli_select_outputs_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "skill_index.py"),
            "select",
            "--level",
            "L3",
            "--role",
            "architect",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert names(payload) == {"hermes-architect"}
    assert payload[0]["tags"] == ["architecture", "adr", "design"]


def test_on_demand_browser_skill_loads_only_for_supplier_browser_tasks() -> None:
    manifest = load_manifest()

    generic = select_skill_context(
        manifest,
        route={
            "task_level": "L2",
            "task_type": "standard_task",
            "risk_level": "medium",
            "review_required": False,
            "human_gate_required": False,
            "process_plan": ["router", "supervisor", "bot1"],
        },
    )
    supplier = select_skill_context(
        manifest,
        route={
            "task_level": "L2",
            "task_type": "supplier_price_deadline_analysis",
            "risk_level": "high",
            "review_required": True,
            "human_gate_required": False,
            "process_plan": ["router", "supervisor", "bot1", "tester", "bot2_light_if_risky"],
        },
    )

    assert "hermes-browser" not in names(generic["selected_skills"])
    assert "hermes-browser" in names(supplier["selected_skills"])


def test_on_demand_browser_selection_is_not_sticky_after_cache() -> None:
    clear_caches()
    manifest = load_manifest()
    supplier_route = {
        "task_level": "L2",
        "task_type": "supplier_price_deadline_analysis",
        "risk_level": "high",
        "review_required": True,
        "human_gate_required": False,
        "process_plan": ["router", "supervisor", "bot1", "tester", "bot2_light_if_risky"],
    }
    generic_route = {
        "task_level": "L2",
        "task_type": "standard_task",
        "risk_level": "medium",
        "review_required": False,
        "human_gate_required": False,
        "process_plan": ["router", "supervisor", "bot1"],
    }

    supplier = select_skill_context(manifest, route=supplier_route)
    generic = select_skill_context(manifest, route=generic_route)

    assert "hermes-browser" in names(supplier["selected_skills"])
    assert "hermes-browser" not in names(generic["selected_skills"])
    assert cache_stats()["context_entries"] == 2


def test_skill_context_selects_by_route_and_marks_gated_devops() -> None:
    manifest = load_manifest()
    route = {
        "task_level": "L4",
        "task_type": "git_write_or_deploy",
        "risk_level": "high",
        "review_required": True,
        "human_gate_required": True,
        "process_plan": ["router", "supervisor", "architect", "bot1", "tester", "bot2", "devops_if_approved"],
    }

    context = select_skill_context(manifest, route=route)

    assert context["selection_policy"] == "lazy_by_task_level_role_and_tags"
    assert context["task_tags"] == ["deploy", "devops", "github", "merge", "pull-request"]
    assert context["roles"]["bot1"][0]["name"] == "hermes-developer"
    assert names(context["selected_skills"]) == {
        "role-dispatcher",
        "hermes-architect",
        "hermes-developer",
        "hermes-tester",
        "github-code-review",
    }
    assert names(context["gated_skills"]) == {"github-pr-workflow", "hermes-devops"}
    assert context["gated_roles"]["devops"][0]["gateway_required"] is True
    assert context["runtime_contract"]["do_not_load_full_skills_tree"] is True


def test_skill_context_cache_returns_isolated_copies() -> None:
    clear_caches()
    manifest = load_manifest()
    route = {
        "task_level": "L1",
        "task_type": "simple_text_task",
        "risk_level": "low",
        "review_required": False,
        "human_gate_required": False,
        "process_plan": ["router", "supervisor", "bot1"],
    }

    first = select_skill_context(manifest, route=route)
    first["selected_skills"].append({"name": "mutated"})
    second = select_skill_context(manifest, route=route)

    assert cache_stats()["manifest_entries"] >= 1
    assert cache_stats()["context_entries"] >= 1
    assert names(second["selected_skills"]) == {"hermes-developer"}


def test_skill_index_cli_context_outputs_runtime_contract() -> None:
    route = {
        "task_level": "L1",
        "task_type": "simple_text_task",
        "risk_level": "low",
        "review_required": False,
        "human_gate_required": False,
        "process_plan": ["router", "supervisor", "bot1"],
    }
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "skill_index.py"),
            "context",
            "--route-json",
            json.dumps(route),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert names(payload["selected_skills"]) == {"hermes-developer"}
    assert payload["roles"]["bot1"][0]["path"] == "skills/hermes-developer/SKILL.md"
    assert payload["gated_skills"] == []
