"""Unit tests for suite_harness comparison helpers (top-15 #15).

Converts the dotted-path comparator from opaque subprocess pass/fail into real
line coverage.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from suite_harness import assert_fields, case_status, value_at  # noqa: E402


def test_value_at_walks_nested_dicts() -> None:
    assert value_at({"a": {"b": {"c": 1}}}, "a.b.c") == 1


def test_value_at_missing_key_returns_none() -> None:
    assert value_at({"a": {"b": 1}}, "a.x") is None


def test_value_at_stops_at_non_dict() -> None:
    assert value_at({"a": "string"}, "a.b") is None


def test_assert_fields_all_match_returns_empty() -> None:
    actual = {"summary": {"status": "approved", "task_level": "L2"}}
    expected = {"summary.status": "approved", "summary.task_level": "L2"}
    assert assert_fields(actual, expected) == []


def test_assert_fields_reports_mismatch() -> None:
    actual = {"summary": {"status": "failed"}}
    failures = assert_fields(actual, {"summary.status": "approved"})
    assert len(failures) == 1
    assert "summary.status" in failures[0]


def test_case_status_prefers_summary_then_gateway_reason() -> None:
    assert case_status({"summary": {"status": "approved"}}) == "approved"
    assert case_status({"gateway": {"reason": "missing_supervisor_task_id"}}) == "missing_supervisor_task_id"
    assert case_status({}) == ""
