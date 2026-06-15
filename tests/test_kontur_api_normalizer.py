from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import kontur_api_normalizer as normalizer  # noqa: E402


def test_normalize_grid_response_extracts_useful_fields_from_kontur_shapes() -> None:
    raw = {
        "total": 109,
        "items": [
            {
                "id": "IS38187218",
                "orderNameHighlights": ["Оказание услуг по сбору и реализации <em>лома</em>"],
                "customerName": "Казенное учреждение",
                "customerInn": "8601000000",
                "statusName": "Размещение завершено",
                "requestEndDate": "2023-07-31T04:00:00.0000000Z",
                "maxPrice": {"amount": 123456.78, "currency": "RUB"},
                "lawName": "223-ФЗ",
                "regionName": "ХМАО",
                "url": "/IS38187218",
                "snippet": "<b>продажа</b> лома Р6М5",
            }
        ],
    }

    payload = normalizer.normalize_response(raw, search_text="реализация лома", date_from="01.01.2020")

    item = payload["purchases"][0]
    assert payload["total"] == 109
    assert payload["downloaded"] == 1
    assert item["name"] == "Оказание услуг по сбору и реализации лома"
    assert item["customer"] == "Казенное учреждение"
    assert item["inn"] == "8601000000"
    assert item["status"] == "Размещение завершено"
    assert item["deadline"] == "2023-07-31T04:00:00.0000000Z"
    assert item["price"] == "123456.78"
    assert item["law"] == "223-ФЗ"
    assert item["place"] == "ХМАО"
    assert item["url"] == "https://zakupki.kontur.ru/IS38187218"
    assert item["snippet"] == "продажа лома Р6М5 Оказание услуг по сбору и реализации лома"


def test_cli_normalizes_file_with_limit(tmp_path: Path) -> None:
    source = tmp_path / "raw.json"
    output = tmp_path / "data.json"
    source.write_text(
        json.dumps(
            {
                "result": {
                    "TotalCount": 2,
                    "Rows": [
                        {"purchaseId": "IS1", "orderName": "one"},
                        {"purchaseId": "IS2", "orderName": "two"},
                    ],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    code = normalizer.main(
        [
            str(source),
            "--output",
            str(output),
            "--limit",
            "1",
        ],
    )

    assert code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["total"] == 2
    assert payload["downloaded"] == 1
    assert payload["purchases"][0]["url"] == "https://zakupki.kontur.ru/IS1"


def test_normalize_nested_and_alias_fields_without_blank_other_fields() -> None:
    raw = {
        "Data": {
            "recordsTotal": "1",
            "data": [
                {
                    "purchaseId": "S123",
                    "title": {"text": "Отходы быстрорежущей стали"},
                    "customerInfo": {"inn": "7701000000"},
                    "organizerName": ["ООО", "Металл"],
                    "status": "Прием заявок",
                    "endDate": "2026-06-20",
                    "priceInfo": {"price": {"value": "не указана"}},
                    "fz": "Коммерческая",
                    "region": {"name": "Москва"},
                    "descriptionHighlights": ["<em>Р18</em>"],
                }
            ],
        }
    }

    payload = normalizer.normalize_response(raw)
    item = payload["purchases"][0]

    assert payload["total"] == 1
    assert item["name"] == "Отходы быстрорежущей стали"
    assert item["customer"] == "ООО Металл"
    assert item["inn"] == "7701000000"
    assert item["price"] == "не указана"
    assert item["law"] == "Коммерческая"
    assert item["place"] == "Москва"
    assert item["url"] == "https://zakupki.kontur.ru/S123"
    assert item["snippet"] == "Р18"


def test_extractors_tolerate_unknown_roots_and_values(capsys, tmp_path: Path) -> None:
    assert normalizer.extract_items({"unknown": {"items": []}}) == []
    assert normalizer.extract_items("not-json-object") == []
    assert normalizer.extract_total({"total": "bad"}, 7) == 7
    assert normalizer.normalize_url("") == ""
    assert normalizer.normalize_price({"missing": 1}) == ""
    assert normalizer.clean_text({"missing": "value"}) == ""

    source = tmp_path / "raw-list.json"
    source.write_text(json.dumps([{"orderId": "IS77", "subject": "Д16Т"}], ensure_ascii=False), encoding="utf-8")
    assert normalizer.main([str(source), "--search-text", "Д16Т"]) == 0
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert payload["searchText"] == "Д16Т"
    assert payload["purchases"][0]["name"] == "Д16Т"
