#!/usr/bin/env python3
"""Human-paced browser action policy helpers."""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


PACED_ACTIONS = {
    "start",
    "goto",
    "click",
    "type",
    "wait_for_selector",
    "evaluate",
    "source",
    "screenshot",
    "cookies",
}

PROFILE_DEFAULTS = {
    "human": (1.0, 2.0),
    "kontur": (1.25, 2.5),
    "cautious": (2.0, 5.0),
    "slow": (3.0, 7.0),
    "bulk": (0.5, 1.5),
}


@dataclass(frozen=True)
class PacePolicy:
    profile: str
    min_delay_seconds: float
    max_delay_seconds: float
    paced_actions: frozenset[str] = frozenset(PACED_ACTIONS)

    @property
    def enabled(self) -> bool:
        return self.profile != "off" and self.max_delay_seconds > 0


def env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def policy_from_values(
    *,
    profile: str | None = None,
    min_delay_seconds: float | None = None,
    max_delay_seconds: float | None = None,
) -> PacePolicy:
    selected = (profile or os.environ.get("HERMES_BROWSER_PACE_PROFILE", "human")).strip().lower()
    if selected in {"0", "false", "no", "off", "none"}:
        return PacePolicy("off", 0.0, 0.0)
    if selected not in PROFILE_DEFAULTS:
        selected = "human"

    default_min, default_max = PROFILE_DEFAULTS[selected]
    minimum = env_float("HERMES_BROWSER_MIN_DELAY_SECONDS", default_min, minimum=0.0)
    maximum = env_float("HERMES_BROWSER_MAX_DELAY_SECONDS", default_max, minimum=minimum)
    if min_delay_seconds is not None:
        minimum = max(0.0, float(min_delay_seconds))
    if max_delay_seconds is not None:
        maximum = max(minimum, float(max_delay_seconds))
    return PacePolicy(selected, minimum, maximum)


def last_action_epoch(state_path: Path) -> float | None:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    value = payload.get("last_action_epoch")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def deterministic_delay(policy: PacePolicy, *, session_id: str, action: str, previous_epoch: float) -> float:
    if policy.max_delay_seconds <= policy.min_delay_seconds:
        return policy.min_delay_seconds
    seed = f"{session_id}:{action}:{int(previous_epoch * 1000)}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    fraction = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return policy.min_delay_seconds + (policy.max_delay_seconds - policy.min_delay_seconds) * fraction


def pace_before_action(
    *,
    session_id: str,
    state_path: Path,
    action: str,
    policy: PacePolicy,
    now: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    now = now or time.time
    sleeper = sleeper or time.sleep
    event: dict[str, Any] = {
        "profile": policy.profile,
        "enabled": policy.enabled and action in policy.paced_actions,
        "action": action,
        "slept_seconds": 0.0,
        "target_delay_seconds": 0.0,
    }
    if not event["enabled"]:
        return event

    previous = last_action_epoch(state_path)
    if previous is None:
        event["reason"] = "first_action"
        return event

    current = now()
    elapsed = max(0.0, current - previous)
    target = deterministic_delay(policy, session_id=session_id, action=action, previous_epoch=previous)
    wait_for = max(0.0, target - elapsed)
    event.update(
        {
            "elapsed_since_previous_seconds": round(elapsed, 3),
            "target_delay_seconds": round(target, 3),
            "slept_seconds": round(wait_for, 3),
        }
    )
    if wait_for > 0:
        sleeper(wait_for)
    return event
