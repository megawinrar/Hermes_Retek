"""Characterization tests for supervisor_common Bot#2 verdict parsing.

The trust boundary between free-text LLM output and the state machine.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from supervisor_common import (  # noqa: E402
    INVALID_BOT2_STATUS,
    extract_bot2_verdict,
    parse_bot2_verdict,
)


def test_valid_approve_defaults_action_execute() -> None:
    verdict = parse_bot2_verdict('{"status": "APPROVE", "summary": "ok"}')
    assert verdict["status"] == "APPROVE"
    assert verdict["approved_action"] == "execute"


def test_status_is_uppercased() -> None:
    verdict = parse_bot2_verdict('{"status": "request_changes"}')
    assert verdict["status"] == "REQUEST_CHANGES"


def test_fenced_json_block_is_unwrapped() -> None:
    verdict = parse_bot2_verdict('```json\n{"status": "REJECT"}\n```')
    assert verdict["status"] == "REJECT"


def test_invalid_json_is_marked_invalid() -> None:
    verdict = parse_bot2_verdict("not json at all")
    assert verdict["status"] == INVALID_BOT2_STATUS
    assert verdict["risks"] == ["invalid_json"]


def test_json_array_is_not_object() -> None:
    verdict = parse_bot2_verdict("[1, 2, 3]")
    assert verdict["status"] == INVALID_BOT2_STATUS
    assert verdict["risks"] == ["json_not_object"]


def test_unknown_status_is_invalid() -> None:
    verdict = parse_bot2_verdict('{"status": "MAYBE"}')
    assert verdict["status"] == INVALID_BOT2_STATUS
    assert verdict["risks"] == ["unknown_status:MAYBE"]


def test_extract_recovers_embedded_object_from_prose() -> None:
    raw = 'Here is my verdict: {"status": "APPROVE_WITH_EVIDENCE"} done.'
    verdict = extract_bot2_verdict(raw)
    assert verdict["status"] == "APPROVE_WITH_EVIDENCE"


def test_extract_falls_back_to_invalid_when_nothing_parses() -> None:
    verdict = extract_bot2_verdict("absolutely no json here")
    assert verdict["status"] == INVALID_BOT2_STATUS
