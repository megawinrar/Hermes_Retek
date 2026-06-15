#!/usr/bin/env python3
"""Kontur UI-first search strategy helpers."""

from __future__ import annotations

import argparse
import json
import re
from typing import Iterable
from urllib.parse import parse_qs, urlparse


EXPIRED_LINK_RE = re.compile(
    r"(срок\s+действия\s+ссылк[аи]\s+ист[её]к|link\s+expired|expired\s+link)",
    re.I,
)


def extract_search_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for name in ("searchId", "searchid", "search_id"):
        values = query.get(name)
        if values and values[0].strip():
            return values[0].strip()
    return ""


def has_expired_link_page(page_text: str) -> bool:
    return bool(EXPIRED_LINK_RE.search(page_text or ""))


def reject_api_query_id_as_search_id(api_payload: dict[str, object]) -> str:
    for name in ("queryId", "QueryId", "query_id"):
        if api_payload.get(name):
            raise ValueError("Kontur API queryId is not a browser searchId; create searchId through the UI first")
    return ""


def build_ui_search_plan(*, keywords: str, current_url: str = "", page_text: str = "") -> dict[str, object]:
    search_id = extract_search_id_from_url(current_url)
    expired = has_expired_link_page(page_text)
    return {
        "strategy": "ui_first_search_then_api_pagination",
        "keywords": keywords,
        "search_id": search_id,
        "search_id_source": "url_after_ui_find_click" if search_id else "",
        "expired_link_detected": expired,
        "next_step": "paginate_api" if search_id and not expired else "open_grid_enter_keywords_click_find",
        "rules": [
            "enter keywords through UI",
            "click Найти",
            "read searchId from resulting URL",
            "do not use API queryId as browser searchId",
            "capture screenshot and page text if expired-link page appears",
        ],
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keywords", required=True)
    parser.add_argument("--url", default="")
    parser.add_argument("--page-text", default="")
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(
        json.dumps(
            build_ui_search_plan(keywords=args.keywords, current_url=args.url, page_text=args.page_text),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
