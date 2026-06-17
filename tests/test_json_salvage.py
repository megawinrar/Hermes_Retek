"""Unit tests for the shared json_salvage helpers."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from json_salvage import brace_objects, fenced_json_blocks, strip_json_fence  # noqa: E402


def test_strip_json_fence_unwraps_single_block() -> None:
    assert strip_json_fence('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_json_fence_without_lang_tag() -> None:
    assert strip_json_fence('```\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_json_fence_passthrough_when_not_fenced() -> None:
    assert strip_json_fence('  {"a": 1}  ') == '{"a": 1}'


def test_strip_json_fence_passthrough_when_prose_around_fence() -> None:
    # Not a *single* fenced object -> returned stripped, unchanged.
    raw = 'text ```json\n{"a": 1}\n``` more'
    assert strip_json_fence(raw) == raw.strip()


def test_fenced_json_blocks_finds_all() -> None:
    raw = 'a ```json\n{"x": 1}\n``` b ```\n{"y": 2}\n``` c'
    assert fenced_json_blocks(raw) == ['{"x": 1}', '{"y": 2}']


def test_brace_objects_extracts_embedded_object() -> None:
    raw = 'verdict: {"status": "APPROVE"} end'
    assert brace_objects(raw) == ['{"status": "APPROVE"}']


def test_brace_objects_tolerates_one_level_of_nesting() -> None:
    raw = 'x {"a": {"b": 1}} y'
    assert brace_objects(raw) == ['{"a": {"b": 1}}']
