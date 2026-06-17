#!/usr/bin/env python3
"""Single source of truth for task complexity levels and risk ordering.

Previously L0..L4 and the rank maps were hand-maintained in both task_router
and skill_index, which risked silent divergence.
"""

from __future__ import annotations


LEVELS: tuple[str, ...] = ("L0", "L1", "L2", "L3", "L4")
LEVEL_RANK: dict[str, int] = {level: index for index, level in enumerate(LEVELS)}
RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
