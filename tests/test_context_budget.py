"""Tests for Hermes context budget helper."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.context_budget import build_context_budget_event, context_usage, estimate_tokens  # noqa: E402


def test_estimate_tokens_uses_ceil_chars_divided_by_four():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2


def test_context_usage_threshold_stages_and_actions():
    assert context_usage(29, 100)["stage"] == "normal"
    assert context_usage(30, 100)["stage"] == "checkpoint_refs"
    assert context_usage(50, 100)["stage"] == "summarize_old_evidence"
    assert context_usage(70, 100)["stage"] == "stop_new_discovery"
    usage = context_usage(80, 100)

    assert usage["stage"] == "force_checkpoint"
    assert usage["actions"] == [
        "checkpoint_refs",
        "summarize_old_evidence",
        "stop_new_discovery",
        "force_checkpoint",
    ]


def test_context_usage_accepts_provider_token_counts():
    usage = context_usage(1234, 4096)

    assert usage["used_tokens"] == 1234
    assert usage["max_tokens"] == 4096
    assert usage["ratio"] == pytest.approx(1234 / 4096)
    assert usage["percent"] == pytest.approx((1234 / 4096) * 100)


def test_build_context_budget_event_omits_raw_text():
    raw_text = "secret evidence that must not be emitted"
    event = build_context_budget_event(
        process_id="proc-1",
        max_tokens=100,
        text=raw_text,
    )
    encoded = json.dumps(event, ensure_ascii=False)

    assert event["used_tokens"] == estimate_tokens(raw_text)
    assert event["text_chars"] == len(raw_text)
    assert raw_text not in encoded
    assert "secret evidence" not in encoded


def test_invalid_max_tokens_rejected():
    with pytest.raises(ValueError):
        context_usage(1, 0)
    with pytest.raises(ValueError):
        build_context_budget_event("proc-1", used_tokens=1, max_tokens=-1)


def test_cli_estimate_prints_json_without_raw_text():
    raw_text = "do not leak this raw prompt"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/context_budget.py",
            "estimate",
            "--max-tokens",
            "100",
            "--text",
            raw_text,
            "--process-id",
            "proc-cli",
        ],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["process_id"] == "proc-cli"
    assert payload["used_tokens"] == estimate_tokens(raw_text)
    assert raw_text not in result.stdout


def test_cli_estimate_reads_text_file_without_raw_text(tmp_path):
    raw_text = "file content should stay private"
    text_file = tmp_path / "context.txt"
    text_file.write_text(raw_text, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/context_budget.py",
            "estimate",
            "--max-tokens",
            "100",
            "--text-file",
            str(text_file),
        ],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout)["used_tokens"] == estimate_tokens(raw_text)
    assert raw_text not in result.stdout


def test_cli_invalid_max_tokens_exits_nonzero():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/context_budget.py",
            "estimate",
            "--max-tokens",
            "0",
            "--text",
            "hello",
        ],
        cwd=os.path.join(os.path.dirname(__file__), ".."),
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "max_tokens must be greater than 0" in result.stderr
