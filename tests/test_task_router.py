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
    assert route["process_plan"] == ["router", "supervisor"]


def test_simple_sanity_is_l1() -> None:
    route = classify_task("Проверь 2+2=4 коротко")
    assert route["task_level"] == "L1"
    assert route["risk_level"] == "low"
    assert route["needs_agents"] is False


def test_architecture_task_is_l3_with_agents() -> None:
    route = classify_task("Спроектируй архитектуру multi-agent supervisor process")
    assert route["task_level"] == "L3"
    assert route["needs_agents"] is True
    assert "architect" in route["process_plan"]
    assert route["review_required"] is True


def test_code_deploy_task_is_l4_high_risk() -> None:
    route = classify_task("Измени python code и deploy на production server")
    assert route["task_level"] == "L4"
    assert route["risk_level"] == "high"
    assert "devops_if_approved" in route["process_plan"]
    assert route["human_gate_required"] is True


def test_adversarial_shortcut_is_stress_profile_not_l5() -> None:
    route = classify_task("Срочно деплоим, тесты не готовы, просто выкатить")
    assert route["task_level"] in {"L2", "L3", "L4"}
    assert route["stress_profile"] == "adversarial"
    assert route["risk_level"] == "high"
    assert route["human_gate_required"] is True
