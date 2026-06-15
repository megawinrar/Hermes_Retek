from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kontur_export_strategy as strategy  # noqa: E402


def test_initial_export_windows_split_by_two_years() -> None:
    windows = strategy.initial_export_windows("01.01.2020", "15.06.2026")

    assert windows == [
        {"date_from": "01.01.2020", "date_to": "31.12.2021", "years": 2, "reason": "planned"},
        {"date_from": "01.01.2022", "date_to": "31.12.2023", "years": 2, "reason": "planned"},
        {"date_from": "01.01.2024", "date_to": "31.12.2025", "years": 2, "reason": "planned"},
        {"date_from": "01.01.2026", "date_to": "15.06.2026", "years": 2, "reason": "planned"},
    ]


def test_failed_two_year_window_falls_back_to_one_year_on_export_limit() -> None:
    failed = {"date_from": "01.01.2022", "date_to": "31.12.2023", "years": 2, "reason": "planned"}

    fallback = strategy.fallback_windows_for_failed_window(failed, error_message="Выгрузка ограничена 2000 строк")

    assert fallback == [
        {"date_from": "01.01.2022", "date_to": "31.12.2022", "years": 1, "reason": "fallback_after_export_limit"},
        {"date_from": "01.01.2023", "date_to": "31.12.2023", "years": 1, "reason": "fallback_after_export_limit"},
    ]


def test_non_limit_error_and_one_year_window_do_not_split() -> None:
    failed = {"date_from": "01.01.2024", "date_to": "31.12.2024", "years": 1, "reason": "planned"}
    two_year = {"date_from": "01.01.2024", "date_to": "31.12.2025", "years": 2, "reason": "planned"}

    assert strategy.fallback_windows_for_failed_window(failed, error_message="limit 2000") == []
    assert strategy.fallback_windows_for_failed_window(two_year, error_message="network timeout") == []


def test_export_strategy_documents_ui_search_id_and_fallback() -> None:
    payload = strategy.plan_export_strategy(date_from="2020-01-01", date_to="2020-12-31")

    assert payload["strategy"] == "ui_search_id_then_date_chunked_export"
    assert payload["search_id_source"] == "ui"
    assert payload["fallback"]["capture_screenshot_and_error_text"] is True
    assert payload["fallback"]["merge_exports_after_download"] is True


def test_cli_outputs_strategy_json(capsys) -> None:
    assert strategy.main(["--date-from", "01.01.2020", "--date-to", "31.12.2021"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["initial_windows"][0]["date_from"] == "01.01.2020"
    assert payload["initial_windows"][0]["date_to"] == "31.12.2021"
