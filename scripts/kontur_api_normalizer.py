#!/usr/bin/env python3
"""Normalize Kontur Zakupki grid API responses into compact purchase records."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return clean_text(" ".join(clean_text(item) for item in value))
    if isinstance(value, dict):
        for key in ("text", "value", "name", "title"):
            if key in value:
                return clean_text(value.get(key))
        return ""
    text = TAG_RE.sub("", str(value))
    return SPACE_RE.sub(" ", text).strip()


def first_text(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        text = clean_text(value)
        if text:
            return text
    return ""


def nested_text(item: dict[str, Any], *paths: tuple[str, ...]) -> str:
    for path in paths:
        value: Any = item
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
        text = clean_text(value)
        if text:
            return text
    return ""


def extract_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    for key in ("items", "Items", "purchases", "Purchases", "data", "Data", "rows", "Rows"):
        value = raw.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_items(value)
            if nested:
                return nested
    result = raw.get("result") or raw.get("Result")
    if isinstance(result, dict):
        return extract_items(result)
    return []


def extract_total(raw: Any, fallback: int) -> int:
    if not isinstance(raw, dict):
        return fallback
    for key in ("total", "Total", "totalCount", "TotalCount", "count", "Count", "recordsTotal"):
        value = raw.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    result = raw.get("result") or raw.get("Result")
    if isinstance(result, dict):
        return extract_total(result, fallback)
    return fallback


def normalize_url(value: str) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if text.startswith("/"):
        return "https://zakupki.kontur.ru" + text
    if re.fullmatch(r"(?:IS|S)\d+", text, flags=re.I):
        return f"https://zakupki.kontur.ru/{text}"
    return text


def normalize_price(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("amount", "Amount", "value", "Value", "price", "Price", "sum", "Sum"):
            if key in value:
                return normalize_price(value.get(key))
    return clean_text(value)


def normalize_purchase(item: dict[str, Any]) -> dict[str, Any]:
    name = first_text(
        item,
        "orderName",
        "orderNameHighlights",
        "purchaseName",
        "name",
        "title",
        "subject",
        "lotName",
    )
    customer = first_text(item, "customerName", "customerNameHighlights", "customer", "organizerName", "companyName")
    inn = first_text(item, "customerInn", "inn", "customerINN", "organizerInn") or nested_text(
        item,
        ("customer", "inn"),
        ("customerInfo", "inn"),
        ("purchaser", "inn"),
        ("organization", "inn"),
    )
    price = normalize_price(
        item.get("maxPrice")
        or item.get("initialPrice")
        or item.get("price")
        or item.get("sum")
        or item.get("amount")
        or nested_text(item, ("priceInfo", "price"), ("contract", "price"))
    )
    law = first_text(item, "law", "lawName", "fz", "purchaseLaw", "typeName")
    place = first_text(item, "deliveryPlace", "place", "region", "regionName", "address")
    url = normalize_url(first_text(item, "url", "href", "link", "purchaseUrl", "id", "purchaseId", "orderId"))
    snippets = [
        first_text(item, "snippet", "description", "descriptionHighlights"),
        first_text(item, "orderNameHighlights"),
        first_text(item, "customerNameHighlights"),
    ]
    return {
        "name": name,
        "customer": customer,
        "inn": inn,
        "status": first_text(item, "status", "statusName", "state", "stateName"),
        "deadline": first_text(item, "deadline", "requestEndDate", "endDate", "biddingEndDate", "publicationDate"),
        "price": price,
        "law": law,
        "place": place,
        "url": url,
        "snippet": clean_text(" ".join(snippet for snippet in snippets if snippet)),
    }


def normalize_response(
    raw: Any,
    *,
    search_text: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int | None = None,
) -> dict[str, Any]:
    items = extract_items(raw)
    if limit is not None:
        items = items[: max(0, int(limit))]
    purchases = [normalize_purchase(item) for item in items]
    return {
        "total": extract_total(raw, len(purchases)),
        "searchText": search_text,
        "dateFrom": date_from,
        "dateTo": date_to,
        "downloaded": len(purchases),
        "purchases": purchases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--search-text", default="")
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    raw = json.loads(args.input.read_text(encoding="utf-8"))
    normalized = normalize_response(
        raw,
        search_text=args.search_text,
        date_from=args.date_from,
        date_to=args.date_to,
        limit=args.limit,
    )
    text = json.dumps(normalized, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
