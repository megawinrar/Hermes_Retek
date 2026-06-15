from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_router import apply_classification_audit, classify_task, parse_classification_audit  # noqa: E402


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


def test_migration_write_has_specific_l4_route() -> None:
    route = classify_task("Apply database migration and write schema rollback evidence")
    assert route["task_level"] == "L4"
    assert route["task_type"] == "database_migration_change"
    assert route["risk_level"] == "high"
    assert route["review_required"] is True
    assert route["human_gate_required"] is True


def test_supplier_price_deadline_analysis_has_specific_l2_route() -> None:
    route = classify_task("Проверь CRM Ретек: сравнить поставщиков по цене, срокам доставки и риску задержки для закупки деталей")
    assert route["task_level"] == "L2"
    assert route["task_type"] == "supplier_price_deadline_analysis"
    assert route["risk_level"] == "high"
    assert route["review_required"] is True
    assert route["human_gate_required"] is False


def test_kontur_parsing_terms_route_to_supplier_browser_process() -> None:
    route = classify_task("Начни парсинг zakupki.kontur.ru: реализация Р6М5, продажа лома Р18, скачай Excel")
    assert route["task_level"] == "L2"
    assert route["task_type"] == "supplier_price_deadline_analysis"
    assert route["risk_level"] == "high"
    assert route["review_required"] is True
    assert route["human_gate_required"] is False


def test_kontur_browser_parse_routes_without_price_words() -> None:
    route = classify_task("Спарси Контур по Д16Т и сохрани результаты")
    assert route["task_level"] == "L2"
    assert route["task_type"] == "supplier_price_deadline_analysis"
    assert route["risk_level"] == "high"
    assert route["review_required"] is True
    assert route["human_gate_required"] is False


def test_marketplace_site_search_routes_to_supplier_browser_process() -> None:
    route = classify_task("https://www.b2b-center.ru/market/ вот еще площадка для поиска Р6М5 и Р18")
    assert route["task_level"] == "L2"
    assert route["task_type"] == "supplier_price_deadline_analysis"
    assert route["risk_level"] == "high"
    assert route["review_required"] is True
    assert route["human_gate_required"] is False


def test_generic_parse_does_not_become_supplier_browser_task() -> None:
    route = classify_task("parse local JSON report and summarize fields")
    assert route["task_type"] != "supplier_price_deadline_analysis"


def test_bot2_classification_audit_can_only_raise_route() -> None:
    route = classify_task("rewrite short hello")
    audited = apply_classification_audit(
        route,
        {
            "status": "REQUIRE_HUMAN_GATE",
            "recommended_level": "L4",
            "risk_level": "high",
            "review_required": True,
            "human_gate_required": True,
            "summary": "Mentions production deploy in hidden context.",
        },
    )

    assert audited["task_level"] == "L4"
    assert audited["risk_level"] == "high"
    assert audited["review_required"] is True
    assert audited["human_gate_required"] is True
    assert "devops_if_approved" in audited["process_plan"]
    assert "task_level:L1->L4" in audited["classification_audit"]["applied"]
    assert "risk_level:low->high" in audited["classification_audit"]["applied"]


def test_bot2_classification_audit_cannot_lower_or_relax_route() -> None:
    route = classify_task("merge PR #12, push to main, and deploy production")
    audited = apply_classification_audit(
        route,
        {
            "status": "CONFIRM",
            "recommended_level": "L1",
            "risk_level": "low",
            "review_required": False,
            "human_gate_required": False,
            "summary": "Incorrect attempt to lower.",
        },
    )

    assert audited["task_level"] == "L4"
    assert audited["risk_level"] == "high"
    assert audited["review_required"] is True
    assert audited["human_gate_required"] is True
    assert "task_level:L4->L1" in audited["classification_audit"]["ignored_demotions"]
    assert "risk_level:high->low" in audited["classification_audit"]["ignored_demotions"]


def test_invalid_bot2_classification_audit_requires_review_fail_safe() -> None:
    audit = parse_classification_audit("not json")
    route = classify_task("rewrite short hello")
    audited = apply_classification_audit(route, audit)

    assert audit["status"] == "INVALID_CLASSIFICATION_AUDIT"
    assert audited["task_level"] == "L1"
    assert audited["risk_level"] == "high"
    assert audited["review_required"] is True
    assert audited["human_gate_required"] is False
