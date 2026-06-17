#!/usr/bin/env python3
"""Salvage JSON objects from messy LLM transcripts.

Owns the three brittle regexes that were previously copy-pasted into
task_router, supervisor_common, and dual_bot_suite. Callers keep their own
accept-predicate and fallback; this module only generates candidate strings so
behavior stays identical while the regexes live in one place.
"""

from __future__ import annotations

import re


# A whole string that is exactly one ```json ...``` fenced object.
_FENCE_FULLMATCH = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.S)
# Every ```json ...``` fenced object embedded in a larger transcript (non-greedy).
_FENCE_FINDALL = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
# Balanced-brace object substrings, tolerating one level of nesting.
_BRACE_OBJECTS = re.compile(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", re.S)


def strip_json_fence(raw: str) -> str:
    """Return the inner object if ``raw`` is exactly one fenced block, else the
    stripped input unchanged."""
    stripped = raw.strip()
    match = _FENCE_FULLMATCH.fullmatch(stripped)
    return match.group(1).strip() if match else stripped


def fenced_json_blocks(raw: str) -> list[str]:
    """All ```json ...``` fenced object bodies found in ``raw``, in order."""
    return _FENCE_FINDALL.findall(raw)


def brace_objects(raw: str) -> list[str]:
    """All balanced-brace object substrings in ``raw``, in order."""
    return _BRACE_OBJECTS.findall(raw)
