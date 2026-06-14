#!/usr/bin/env python3
"""Deterministic supplier scoring helper for Hermes process tasks."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Any


METRIC_ALIASES = {
    "cost": ("cost", "costs", "price", "цена", "стоимость", "стоит"),
    "delivery": ("delivery", "deliver", "delivers", "deadline", "lead time", "срок", "доставка", "доставки"),
    "sla": ("sla", "uptime", "availability", "доступность"),
}
DEFAULT_WEIGHTS = {"cost": 1 / 3, "delivery": 1 / 3, "sla": 1 / 3}


@dataclass(frozen=True)
class SupplierOption:
    name: str
    cost: float
    delivery: float
    sla: float


def _to_float(raw: str) -> float:
    cleaned = raw.replace(",", ".").replace(" ", "")
    return float(cleaned)


def _first_number(patterns: list[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return _to_float(match.group("value"))
    return None


def parse_suppliers(task: str) -> list[SupplierOption]:
    suppliers: list[SupplierOption] = []
    segments = [segment.strip(" .") for segment in re.split(r"[;\n]+", task) if segment.strip()]
    for segment in segments:
        cost = _first_number(
            [
                r"\b(?:costs?|price|стоит|цена|стоимость)\s*(?:is|=|:)?\s*(?P<value>\d+(?:[\s.,]\d+)?)\s*(?:m|млн|million|rub|руб)?\b",
                r"\b(?P<value>\d+(?:[\s.,]\d+)?)\s*(?:m|млн|million)?\s*(?:rub|руб)\b",
            ],
            segment,
        )
        delivery = _first_number(
            [
                r"\b(?:delivers?\s+in|delivery|deadline|lead\s*time|срок(?:\s+доставки)?|доставка)\s*(?:is|=|:)?\s*(?P<value>\d+(?:[\s.,]\d+)?)\s*(?:days?|дн|дней|дня)?\b",
                r"\b(?P<value>\d+(?:[\s.,]\d+)?)\s*(?:days?|дн|дней|дня)\b",
            ],
            segment,
        )
        sla = _first_number(
            [
                r"\b(?:with\s+)?(?P<value>\d+(?:[\s.,]\d+)?)\s*%?\s*(?:sla|uptime|availability|доступность)\b",
                r"\b(?:sla|uptime|availability|доступность)\s*(?:is|=|:)?\s*(?P<value>\d+(?:[\s.,]\d+)?)\s*%?\b",
            ],
            segment,
        )
        if cost is None or delivery is None or sla is None:
            continue
        name_source = segment.rsplit(":", 1)[-1].strip()
        name_match = re.match(r"^\s*(?P<name>[A-Za-zА-Яа-яЁё][A-Za-zА-Яа-яЁё0-9_-]*)", name_source)
        name = name_match.group("name") if name_match else f"Supplier {len(suppliers) + 1}"
        suppliers.append(SupplierOption(name=name, cost=cost, delivery=delivery, sla=sla))
    return suppliers


def parse_weights(task: str) -> tuple[dict[str, float], list[str]]:
    weights: dict[str, float] = {}
    warnings: list[str] = []
    for metric, aliases in METRIC_ALIASES.items():
        alias_pattern = "|".join(re.escape(alias) for alias in aliases)
        patterns = [
            rf"\b(?:{alias_pattern})\b[^\d%]{{0,30}}(?P<value>\d+(?:[\s.,]\d+)?)\s*%",
            rf"(?P<value>\d+(?:[\s.,]\d+)?)\s*%[^\w%]{{0,30}}\b(?:{alias_pattern})\b",
        ]
        value = _first_number(patterns, task)
        if value is not None:
            weights[metric] = value
    if not weights:
        warnings.append("No explicit weights found; equal weights were used.")
        return dict(DEFAULT_WEIGHTS), warnings
    missing = [metric for metric in DEFAULT_WEIGHTS if metric not in weights]
    if missing:
        remaining = max(0.0, 100.0 - sum(weights.values()))
        fallback = remaining / len(missing) if remaining > 0 else 100.0 / len(DEFAULT_WEIGHTS)
        for metric in missing:
            weights[metric] = fallback
        warnings.append(f"Missing weights filled for: {', '.join(missing)}.")
    total = sum(weights.values())
    if total <= 0:
        warnings.append("Invalid weights; equal weights were used.")
        return dict(DEFAULT_WEIGHTS), warnings
    normalized = {metric: value / total for metric, value in weights.items()}
    return normalized, warnings


def _normalize(value: float, values: list[float], *, higher_is_better: bool) -> float:
    low = min(values)
    high = max(values)
    if high == low:
        return 3.0
    if higher_is_better:
        return 1 + ((value - low) / (high - low)) * 4
    return 1 + ((high - value) / (high - low)) * 4


def calculate_supplier_scores(task: str, *, precision: int = 3) -> dict[str, Any]:
    suppliers = parse_suppliers(task)
    weights, warnings = parse_weights(task)
    if len(suppliers) < 2:
        return {
            "status": "insufficient_data",
            "tool": "supplier_score_calculator",
            "summary": "Need at least two suppliers with cost, delivery, and SLA values.",
            "warnings": warnings,
            "inputs": {"supplier_count": len(suppliers), "weights": weights},
            "rows": [],
            "ranking": [],
        }

    cost_values = [item.cost for item in suppliers]
    delivery_values = [item.delivery for item in suppliers]
    sla_values = [item.sla for item in suppliers]
    rows: list[dict[str, Any]] = []
    for item in suppliers:
        cost_score = _normalize(item.cost, cost_values, higher_is_better=False)
        delivery_score = _normalize(item.delivery, delivery_values, higher_is_better=False)
        sla_score = _normalize(item.sla, sla_values, higher_is_better=True)
        weighted_score = (
            cost_score * weights["cost"] + delivery_score * weights["delivery"] + sla_score * weights["sla"]
        )
        rows.append(
            {
                "supplier": item.name,
                "inputs": {
                    "cost": item.cost,
                    "delivery_days": item.delivery,
                    "sla": item.sla,
                },
                "normalized": {
                    "cost": round(cost_score, precision),
                    "delivery": round(delivery_score, precision),
                    "sla": round(sla_score, precision),
                },
                "weighted_score": round(weighted_score, precision),
            }
        )
    rows.sort(key=lambda row: (-row["weighted_score"], str(row["supplier"]).lower()))
    return {
        "status": "ok",
        "tool": "supplier_score_calculator",
        "summary": f"Calculated deterministic weighted ranking for {len(rows)} suppliers.",
        "formula": (
            "score = cost_norm*weight_cost + delivery_norm*weight_delivery + sla_norm*weight_sla; "
            "lower cost/delivery is better, higher SLA is better; normalized range is 1..5."
        ),
        "weights": {metric: round(value, precision) for metric, value in weights.items()},
        "rounding": f"{precision} decimals",
        "warnings": warnings,
        "rows": rows,
        "ranking": [row["supplier"] for row in rows],
        "winner": rows[0]["supplier"],
    }


def build_tool_result(task: str, route: dict[str, Any] | None = None) -> dict[str, Any]:
    route = route or {}
    task_type = str(route.get("task_type") or "")
    if task_type and task_type != "supplier_price_deadline_analysis":
        return {"status": "not_applicable", "tool": "supplier_score_calculator", "reason": f"task_type={task_type}"}
    return calculate_supplier_scores(task)


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate deterministic supplier weighted scores")
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    print(json.dumps(calculate_supplier_scores(args.task), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
