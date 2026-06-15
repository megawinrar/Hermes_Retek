from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import browser_pacing  # noqa: E402


def test_human_policy_defaults_to_one_to_two_seconds(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_BROWSER_PACE_PROFILE", raising=False)
    monkeypatch.delenv("HERMES_BROWSER_MIN_DELAY_SECONDS", raising=False)
    monkeypatch.delenv("HERMES_BROWSER_MAX_DELAY_SECONDS", raising=False)

    policy = browser_pacing.policy_from_values()

    assert policy.profile == "human"
    assert policy.min_delay_seconds == 1.0
    assert policy.max_delay_seconds == 2.0


def test_kontur_policy_uses_slower_default_spacing() -> None:
    policy = browser_pacing.policy_from_values(profile="kontur")

    assert policy.profile == "kontur"
    assert policy.min_delay_seconds == 1.25
    assert policy.max_delay_seconds == 2.5


def test_generic_site_profiles_have_distinct_defaults() -> None:
    cautious = browser_pacing.policy_from_values(profile="cautious")
    slow = browser_pacing.policy_from_values(profile="slow")
    bulk = browser_pacing.policy_from_values(profile="bulk")

    assert cautious.min_delay_seconds == 2.0
    assert cautious.max_delay_seconds == 5.0
    assert slow.min_delay_seconds == 3.0
    assert slow.max_delay_seconds == 7.0
    assert bulk.min_delay_seconds == 0.5
    assert bulk.max_delay_seconds == 1.5


def test_pace_before_action_sleeps_when_previous_action_was_recent(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"last_action_epoch": 100.0}), encoding="utf-8")
    slept: list[float] = []
    policy = browser_pacing.PacePolicy("human", 1.0, 1.0)

    event = browser_pacing.pace_before_action(
        session_id="kontur",
        state_path=state,
        action="source",
        policy=policy,
        now=lambda: 100.25,
        sleeper=slept.append,
    )

    assert event["enabled"] is True
    assert event["target_delay_seconds"] == 1.0
    assert event["slept_seconds"] == 0.75
    assert slept == [0.75]


def test_pace_before_action_skips_first_action_and_off_profile(tmp_path: Path) -> None:
    missing_state = tmp_path / "missing.json"

    first = browser_pacing.pace_before_action(
        session_id="kontur",
        state_path=missing_state,
        action="goto",
        policy=browser_pacing.PacePolicy("human", 1.0, 2.0),
        sleeper=lambda _seconds: (_ for _ in ()).throw(AssertionError("should not sleep")),
    )
    off = browser_pacing.pace_before_action(
        session_id="kontur",
        state_path=missing_state,
        action="goto",
        policy=browser_pacing.PacePolicy("off", 0.0, 0.0),
        sleeper=lambda _seconds: (_ for _ in ()).throw(AssertionError("should not sleep")),
    )

    assert first["reason"] == "first_action"
    assert first["slept_seconds"] == 0.0
    assert off["enabled"] is False
