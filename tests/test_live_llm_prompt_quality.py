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


def test_bot_prompt_includes_startup_context_pack_without_cookie_values() -> None:
    cookie = "cookie_" + "C" * 40
    messages = dual_bot_lab.bot1_messages(
        "Change restart guard",
        "Need tests.",
        skill_context={
            "role": "bot1",
            "startup_context_pack": {
                "session_strategy": "fresh_session_with_durable_context_pack",
                "required_fixes": ["Add rollback evidence."],
                "rlm_context": {
                    "context": f"[7] process_summary Restart guard: use safe restart auth.sid={cookie}",
                    "records": [],
                    "estimated_tokens": 12,
                    "token_budget": 120,
                },
            },
        },
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "fresh_session_with_durable_context_pack" in combined
    assert "Add rollback evidence." in combined
    assert "Restart guard" in combined
    assert cookie not in combined
    assert "[REDACTED]" in combined


def test_bot2_prompt_redacts_startup_context_pack_secret_values() -> None:
    api_key = "API_KEY=" + "D" * 32
    bearer = "Authorization: Bearer " + "E" * 32
    cookie = "auth.sid=" + "F" * 32
    messages = dual_bot_lab.bot2_messages(
        "Review restart guard",
        "Need Bot#2 verdict.",
        "Bot#1 result is public.",
        skill_context={
            "role": "bot2",
            "startup_context_pack": {
                "session_strategy": "fresh_session_with_durable_context_pack",
                "required_fixes": [f"Remove leaked {api_key}"],
                "previous_attempts": [
                    {"worker": "bot1", "status": "failed", "error_preview": bearer}
                ],
                "human_decision": {"reason": cookie},
            },
        },
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "fresh_session_with_durable_context_pack" in combined
    assert "Remove leaked" in combined
    assert "error_preview" in combined
    assert api_key not in combined
    assert bearer not in combined
    assert cookie not in combined
    assert combined.count("[REDACTED]") >= 3


def test_live_prompt_builders_redact_task_acceptance_and_bot_outputs() -> None:
    secret = "API_KEY=" + "G" * 32
    bearer = "Authorization: Bearer " + "H" * 32
    cookie = "auth.sid=" + "I" * 32

    prompt_sets = [
        dual_bot_lab.bot1_messages(
            f"Task includes {secret}",
            f"Acceptance includes {bearer}",
        ),
        dual_bot_lab.bot2_messages(
            f"Task includes {secret}",
            f"Acceptance includes {bearer}",
            f"Bot1 leaked {cookie}",
        ),
        dual_bot_lab.bot1_revision_messages(
            f"Task includes {secret}",
            f"Acceptance includes {bearer}",
            f"Previous answer leaked {cookie}",
            {"summary": f"Summary {secret}", "required_fixes": [bearer], "risks": [cookie]},
            2,
        ),
        dual_bot_lab.bot1_self_check_messages(
            f"Task includes {secret}",
            f"Acceptance includes {bearer}",
            f"Draft leaked {cookie}",
            {"required_fixes": [secret], "risks": [bearer]},
            2,
        ),
        dual_bot_lab.bot2_repair_messages(
            f"Task includes {secret}",
            f"Acceptance includes {bearer}",
            f"Bot1 leaked {cookie}",
            f"Invalid review leaked {secret}",
        ),
        dual_bot_lab.bot2_route_audit_messages(
            f"Task includes {secret}",
            {"task_level": "L4", "risk_note": bearer},
        ),
    ]
    combined = "\n".join(message["content"] for messages in prompt_sets for message in messages)

    assert secret not in combined
    assert bearer not in combined
    assert cookie not in combined
    assert combined.count("[REDACTED]") >= 10


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
