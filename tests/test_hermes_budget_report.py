from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import hermes_budget_report  # noqa: E402


def test_build_report_formats_budget_without_llm() -> None:
    report = hermes_budget_report.build_report(
        budget={
            "daily_budget_rub": 1000,
            "spent_today_rub": 12.5,
            "remaining_rub": 987.5,
            "usage_percent": 1.25,
            "is_blocked": False,
            "current_provider": {"provider": "bothub", "model": "deepseek-v4-flash"},
        },
        usage_today={"total_tokens": 12345, "request_count": 67, "total_cost_rub": 12.5},
        history={
            "history": [
                {"date": "2026-06-15", "total_tokens": 12345, "total_cost_rub": 12.5, "request_count": 67},
                {"date": "2026-06-14", "total_tokens": 23456, "total_cost_rub": 23.4, "request_count": 89},
            ]
        },
        health={"provider": {"provider": "bothub", "model": "deepseek-v4-flash"}},
        generated_at=datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc),
    )

    assert "Отчёт Hermes по LLM-бюджету" in report
    assert "Время: 2026-06-15 13:00 UTC" in report
    assert "- статус: OK" in report
    assert "- провайдер: bothub" in report
    assert "- модель: deepseek-v4-flash" in report
    assert "- дневной лимит: 1000.00₽" in report
    assert "- запросов сегодня: 67" in report
    assert "- 2026-06-15: 12345 токенов, 12.50₽, 67 запросов" in report
    assert "Всё хорошо" in report


def test_build_report_marks_blocked_budget() -> None:
    report = hermes_budget_report.build_report(
        budget={"is_blocked": True, "usage_percent": 100},
        usage_today={},
        history=[],
        health={},
        generated_at=datetime(2026, 6, 15, 13, 0, tzinfo=timezone.utc),
    )

    assert "- статус: BLOCKED" in report
    assert "бюджет исчерпан" in report
