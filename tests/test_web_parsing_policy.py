from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import web_parsing_policy as policy  # noqa: E402


def test_kontur_domain_selects_ui_seed_single_request_policy() -> None:
    selected = policy.select_policy(url="https://zakupki.kontur.ru/Grid", task="скачай Excel")

    assert selected["name"] == "kontur"
    assert selected["mode"] == "ui_seed_then_api_pagination"
    assert selected["pace_profile"] == "kontur"
    assert selected["max_parallel_requests"] == 1
    assert selected["chunk_years"] == 2
    assert selected["fallback_chunk_years"] == 1


def test_unknown_authorized_site_uses_cautious_browser_first_policy() -> None:
    selected = policy.select_policy(url="https://example-crm.test/search", task="зайди в аккаунт и выгрузи xlsx")

    assert selected["name"] == "default_authenticated"
    assert selected["mode"] == "browser_first_then_structured_extract"
    assert selected["pace_profile"] == "cautious"
    assert selected["max_parallel_requests"] == 1
    assert selected["requires_ui_seed"] is True
    assert selected["chunk_years"] == 1


def test_unknown_public_site_allows_small_parallel_fetch_with_browser_fallback() -> None:
    selected = policy.select_policy(url="https://example.org/catalog", task="parse public catalog")

    assert selected["name"] == "default_public"
    assert selected["mode"] == "structured_fetch_with_browser_fallback"
    assert selected["pace_profile"] == "human"
    assert selected["max_parallel_requests"] == 2
    assert selected["requires_ui_seed"] is False


def test_cli_outputs_policy_json(capsys) -> None:
    assert policy.main(["--url", "zakupki.kontur.ru", "--task", "export excel"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "kontur"
    assert "pace browser and API actions through the selected delay profile" in payload["rules"]
