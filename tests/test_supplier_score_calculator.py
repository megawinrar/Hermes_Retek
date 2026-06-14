from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from supplier_score_calculator import calculate_supplier_scores, parse_weights  # noqa: E402


SUPPLIER_TASK = (
    "Score CRM Retek supplier options: Alpha costs 1.2M RUB and delivers in 20 days with 99.5 SLA; "
    "Beta costs 0.95M RUB and delivers in 35 days with 98.7 SLA; "
    "Gamma costs 1.05M RUB and delivers in 25 days with 99.1 SLA. "
    "Use weighted scoring for cost 35%, delivery 30%, SLA 35%."
)


def test_supplier_score_calculator_ranks_weighted_options_deterministically() -> None:
    result = calculate_supplier_scores(SUPPLIER_TASK)

    assert result["status"] == "ok"
    assert result["winner"] == "Alpha"
    assert result["ranking"] == ["Alpha", "Gamma", "Beta"]
    assert result["weights"] == {"cost": 0.35, "delivery": 0.3, "sla": 0.35}

    rows = {row["supplier"]: row for row in result["rows"]}
    assert rows["Alpha"]["weighted_score"] == 3.6
    assert rows["Gamma"]["weighted_score"] == 3.34
    assert rows["Beta"]["weighted_score"] == 2.4
    assert rows["Alpha"]["normalized"] == {"cost": 1.0, "delivery": 5.0, "sla": 5.0}


def test_supplier_score_calculator_falls_back_when_weights_missing() -> None:
    weights, warnings = parse_weights("Alpha costs 10 and delivers in 2 days with 99 SLA")

    assert weights == {"cost": 1 / 3, "delivery": 1 / 3, "sla": 1 / 3}
    assert warnings == ["No explicit weights found; equal weights were used."]
