#!/usr/bin/env python3
"""Kontur export window planning helpers."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable


EXPORT_LIMIT_RE = re.compile(
    r"(лимит|limit|too many|слишком много|2000|превыш|exceed|выгрузк|export)",
    re.I,
)


@dataclass(frozen=True)
class DateWindow:
    date_from: str
    date_to: str
    years: int
    reason: str


def parse_date(value: str) -> date:
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        year, month, day = [int(part) for part in text.split("-")]
        return date(year, month, day)
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        day, month, year = [int(part) for part in text.split(".")]
        return date(year, month, day)
    raise ValueError(f"unsupported date format: {value!r}")


def format_date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def year_windows(start: date, end: date, *, years_per_window: int = 2, reason: str = "planned") -> list[DateWindow]:
    if end < start:
        raise ValueError("end date must be on or after start date")
    years = max(1, int(years_per_window))
    windows: list[DateWindow] = []
    current_year = start.year
    while current_year <= end.year:
        window_start = max(start, date(current_year, 1, 1))
        window_end_year = min(current_year + years - 1, end.year)
        window_end = min(end, date(window_end_year, 12, 31))
        windows.append(
            DateWindow(
                date_from=format_date(window_start),
                date_to=format_date(window_end),
                years=years,
                reason=reason,
            )
        )
        current_year = window_end_year + 1
    return windows


def initial_export_windows(date_from: str, date_to: str, *, years_per_window: int = 2) -> list[dict[str, object]]:
    return [asdict(item) for item in year_windows(parse_date(date_from), parse_date(date_to), years_per_window=years_per_window)]


def is_export_limit_error(message: str) -> bool:
    return bool(EXPORT_LIMIT_RE.search(message or ""))


def fallback_windows_for_failed_window(
    window: dict[str, object] | DateWindow,
    *,
    error_message: str = "",
) -> list[dict[str, object]]:
    if isinstance(window, DateWindow):
        data = asdict(window)
    else:
        data = dict(window)
    start = parse_date(str(data["date_from"]))
    end = parse_date(str(data["date_to"]))
    if start.year == end.year:
        return []
    if error_message and not is_export_limit_error(error_message):
        return []
    return [asdict(item) for item in year_windows(start, end, years_per_window=1, reason="fallback_after_export_limit")]


def plan_export_strategy(
    *,
    date_from: str,
    date_to: str,
    search_id_source: str = "ui",
    years_per_window: int = 2,
) -> dict[str, object]:
    windows = initial_export_windows(date_from, date_to, years_per_window=years_per_window)
    return {
        "strategy": "ui_search_id_then_date_chunked_export",
        "search_id_source": search_id_source,
        "date_from": format_date(parse_date(date_from)),
        "date_to": format_date(parse_date(date_to)),
        "initial_years_per_window": years_per_window,
        "initial_windows": windows,
        "fallback": {
            "on_export_limit_or_error": "split_failed_window_to_one_year",
            "capture_screenshot_and_error_text": True,
            "merge_exports_after_download": True,
        },
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date-from", required=True)
    parser.add_argument("--date-to", required=True)
    parser.add_argument("--years-per-window", type=int, default=2)
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(
        json.dumps(
            plan_export_strategy(
                date_from=args.date_from,
                date_to=args.date_to,
                years_per_window=args.years_per_window,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
