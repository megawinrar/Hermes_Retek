"""Tests for dual_bot_suite.extract_verdict (top-15 #13).

dual_bot_suite previously had zero tests. This is its second, independent verdict
parser (requires a "status" key), so it can silently diverge from the canonical
one; pin it.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from dual_bot_suite import extract_verdict  # noqa: E402


def test_extract_from_fenced_json_block() -> None:
    verdict = extract_verdict('```json\n{"status": "APPROVE", "summary": "ok"}\n```')
    assert verdict["status"] == "APPROVE"


def test_extract_from_embedded_object() -> None:
    verdict = extract_verdict('Verdict follows: {"status": "REJECT"} done.')
    assert verdict["status"] == "REJECT"


def test_object_without_status_is_skipped() -> None:
    # extract_verdict requires a "status" key; a status-less object falls through.
    verdict = extract_verdict('{"summary": "no status here"}')
    assert verdict["status"] == "UNPARSEABLE"


def test_no_json_returns_unparseable_fallback() -> None:
    verdict = extract_verdict("there is no json at all in this transcript")
    assert verdict["status"] == "UNPARSEABLE"
    assert verdict["risks"] == ["missing_machine_readable_verdict"]
