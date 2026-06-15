#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


DEFAULT_BASE_URL = "http://hermes-yandex-proxy:8000"
DEFAULT_TIMEOUT_SECONDS = 8.0


class BudgetReportError(RuntimeError):
    pass


def _get_json(base_url: str, path: str, *, timeout: float) -> dict[str, Any] | list[Any]:
    url = base_url.rstrip("/") + path
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310 - internal Docker service URL.
            status = getattr(response, "status", 200)
            payload = response.read().decode("utf-8")
    except URLError as exc:
        raise BudgetReportError(f"{path}: {exc}") from exc
    if status < 200 or status >= 300:
        raise BudgetReportError(f"{path}: HTTP {status}")
    return json.loads(payload)


def _rub(value: Any) -> str:
    try:
        return f"{float(value):.2f}₽"
    except (TypeError, ValueError):
        return "n/a"


def _int(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "n/a"


def _status(budget: dict[str, Any]) -> str:
    if budget.get("is_blocked"):
        return "BLOCKED"
    try:
        usage = float(budget.get("usage_percent") or 0)
    except (TypeError, ValueError):
        usage = 0.0
    if usage >= 95:
        return "LIMIT_NEAR"
    if usage >= 70:
        return "WARNING"
    return "OK"


def build_report(
    *,
    budget: dict[str, Any],
    usage_today: dict[str, Any],
    history: dict[str, Any] | list[Any],
    health: dict[str, Any],
    generated_at: datetime | None = None,
) -> str:
    generated_at = generated_at or datetime.now(timezone.utc)
    provider = health.get("provider") or budget.get("current_provider") or {}
    model = provider.get("model") or "unknown"
    provider_name = provider.get("provider") or "unknown"

    lines = [
        "Отчёт Hermes по LLM-бюджету",
        f"Время: {generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Итог",
        f"- статус: {_status(budget)}",
        f"- провайдер: {provider_name}",
        f"- модель: {model}",
        f"- дневной лимит: {_rub(budget.get('daily_budget_rub') or budget.get('daily_limit_rub'))}",
        f"- потрачено сегодня: {_rub(budget.get('spent_today_rub') or usage_today.get('total_cost_rub'))}",
        f"- осталось: {_rub(budget.get('remaining_rub'))}",
        f"- расход: {budget.get('usage_percent', 'n/a')}%",
        f"- запросов сегодня: {_int(usage_today.get('request_count'))}",
        f"- токенов сегодня: {_int(usage_today.get('total_tokens'))}",
        "",
        "История",
    ]

    rows = history.get("history") if isinstance(history, dict) else history
    if not isinstance(rows, list) or not rows:
        lines.append("- нет данных")
    else:
        for row in rows[:7]:
            if not isinstance(row, dict):
                continue
            lines.append(
                "- "
                f"{row.get('date', 'n/a')}: "
                f"{_int(row.get('total_tokens'))} токенов, "
                f"{_rub(row.get('total_cost_rub'))}, "
                f"{_int(row.get('request_count'))} запросов"
            )

    if budget.get("is_blocked"):
        lines.extend(["", "Внимание: бюджет исчерпан, требуется пополнение."])
    else:
        try:
            usage_percent = float(budget.get("usage_percent") or 0)
        except (TypeError, ValueError):
            usage_percent = 0.0
        if usage_percent > 70:
            lines.extend(["", "Внимание: расход выше 70% дневного лимита."])
        elif usage_percent < 30:
            lines.extend(["", "Всё хорошо: расход ниже 30% дневного лимита."])

    return "\n".join(lines).strip() + "\n"


def fetch_report(base_url: str, *, timeout: float) -> str:
    budget = _get_json(base_url, "/v1/budget", timeout=timeout)
    usage_today = _get_json(base_url, "/v1/usage/today", timeout=timeout)
    history = _get_json(base_url, "/v1/usage/history?days=3", timeout=timeout)
    health = _get_json(base_url, "/health", timeout=timeout)
    if not all(isinstance(item, dict) for item in [budget, usage_today, health]):
        raise BudgetReportError("unexpected JSON shape from budget endpoints")
    return build_report(
        budget=budget,
        usage_today=usage_today,
        history=history,
        health=health,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print deterministic Hermes LLM budget report")
    parser.add_argument("--base-url", default=os.environ.get("HERMES_BUDGET_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("HERMES_BUDGET_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)))
    args = parser.parse_args(argv)

    try:
        sys.stdout.write(fetch_report(args.base_url, timeout=args.timeout))
    except Exception as exc:
        sys.stdout.write(f"Отчёт Hermes по LLM-бюджету\nСтатус: ERROR\nОшибка: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
