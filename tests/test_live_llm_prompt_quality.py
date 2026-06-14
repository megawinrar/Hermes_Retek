from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_bot_lab  # noqa: E402


def test_bot1_prompt_preserves_retek_domain_name() -> None:
    messages = dual_bot_lab.bot1_messages(
        "Для CRM Ретек составь матрицу оценки поставщиков",
        "Не искажать название CRM Ретек.",
    )
    combined = "\n".join(message["content"] for message in messages)

    assert 'written in Russian as "Ретек"' in combined
    assert "CRM Ретек" in combined
    assert "Do not substitute" in combined


def test_bot1_prompt_accepts_semantic_budget_by_task_level() -> None:
    semantic_budget = dual_bot_lab.semantic_budget_for_route(
        {"task_level": "L3", "task_type": "database_migration_plan", "risk_level": "high"},
        "bot1",
    )
    messages = dual_bot_lab.bot1_messages(
        "Plan SQLite to PostgreSQL migration",
        "Need rollback and tests.",
        semantic_budget=semantic_budget,
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "meaning-first compression" in combined
    assert "implementation_or_migration_plan" in combined
    assert "rollback" in combined
    assert "tutorial background" in combined


def test_bot1_l2_prompt_uses_compact_math_output_contract() -> None:
    semantic_budget = dual_bot_lab.semantic_budget_for_route(
        {"task_level": "L2", "task_type": "supplier_price_deadline_analysis", "risk_level": "high"},
        "bot1",
    )
    messages = dual_bot_lab.bot1_messages(
        "Score suppliers with weighted normalization",
        "Need ranked suppliers and data risks.",
        semantic_budget=semantic_budget,
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "one formula, one compact table, and one final ranking" in combined
    assert "one rounding rule" in combined
    assert "finish without truncation" in combined


def test_bot1_prompt_includes_deterministic_tool_results() -> None:
    messages = dual_bot_lab.bot1_messages(
        "Score suppliers",
        "Need ranked suppliers.",
        skill_context={
            "role": "bot1",
            "tool_results": [
                {
                    "tool": "supplier_score_calculator",
                    "status": "ok",
                    "winner": "Alpha",
                    "ranking": ["Alpha", "Gamma", "Beta"],
                }
            ],
        },
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "supplier_score_calculator" in combined
    assert '"winner": "Alpha"' in combined
    assert '"ranking": [' in combined


def test_bot2_prompt_does_not_require_future_supervisor_transcript() -> None:
    messages = dual_bot_lab.bot2_messages(
        "Live LLM smoke for CRM Ретек supplier analysis",
        "Supervisor transcript should show both bots after the run.",
        "Bot#1 answer with matrix and risks.",
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "Supervisor transcript is generated after Bot#2 returns its verdict" in combined
    assert "must not mark a result as insufficient solely" in combined
    assert "future transcript is not embedded inside Bot#1" in combined


def test_bot2_semantic_budget_is_issue_budget_not_token_cut() -> None:
    semantic_budget = dual_bot_lab.semantic_budget_for_route(
        {"task_level": "L2", "task_type": "supplier_scoring", "risk_level": "medium"},
        "bot2",
    )
    messages = dual_bot_lab.bot2_messages(
        "Score suppliers",
        "Need inverse normalization.",
        "Bot#1 answer",
        semantic_budget=semantic_budget,
    )
    combined = "\n".join(message["content"] for message in messages)

    assert '"issue_budget": 2' in combined
    assert "Find only blocking semantic defects" in combined
    assert "style-only objections" in combined
