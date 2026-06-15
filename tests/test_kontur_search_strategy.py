from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kontur_search_strategy as strategy  # noqa: E402


def test_extract_search_id_only_from_ui_result_url() -> None:
    url = "https://zakupki.kontur.ru/Grid/Search?searchId=abc-123&query=%D0%A016"

    assert strategy.extract_search_id_from_url(url) == "abc-123"
    assert strategy.extract_search_id_from_url("https://zakupki.kontur.ru/Grid") == ""


def test_reject_api_query_id_as_browser_search_id() -> None:
    with pytest.raises(ValueError, match="queryId is not a browser searchId"):
        strategy.reject_api_query_id_as_search_id({"queryId": "api-only-id"})


def test_expired_link_page_forces_ui_restart() -> None:
    plan = strategy.build_ui_search_plan(
        keywords="реализация Р6М5",
        current_url="https://zakupki.kontur.ru/Grid/Search?searchId=old",
        page_text="Срок действия ссылки истёк",
    )

    assert plan["search_id"] == "old"
    assert plan["expired_link_detected"] is True
    assert plan["next_step"] == "open_grid_enter_keywords_click_find"


def test_valid_ui_search_url_allows_api_pagination() -> None:
    plan = strategy.build_ui_search_plan(
        keywords="Д16Т",
        current_url="https://zakupki.kontur.ru/Grid/Search?searchId=valid",
    )

    assert plan["search_id_source"] == "url_after_ui_find_click"
    assert plan["next_step"] == "paginate_api"


def test_cli_outputs_ui_search_plan(capsys) -> None:
    assert strategy.main(["--keywords", "Р18", "--url", "https://zakupki.kontur.ru/Grid?searchId=s1"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["keywords"] == "Р18"
    assert payload["search_id"] == "s1"
