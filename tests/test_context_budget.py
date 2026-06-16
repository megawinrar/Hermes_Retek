"""Tests for Hermes context budget helper."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import scripts.context_budget as context_budget  # noqa: E402
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


def test_context_circuit_breaker_absolute_thresholds():
    assert context_budget.context_circuit_breaker(59_999)["stage"] == "ok"
    warn = context_budget.context_circuit_breaker(60_000)
    hard = context_budget.context_circuit_breaker(80_000)
    blocked = context_budget.context_circuit_breaker(120_000)

    assert warn["stage"] == "compress_before_next_turn"
    assert warn["should_call_provider"] is True
    assert hard["stage"] == "force_fresh_session"
    assert hard["should_start_fresh_session"] is True
    assert blocked["stage"] == "block_llm"
    assert blocked["should_call_provider"] is False
    assert "do_not_call_provider" in blocked["actions"]


def test_context_circuit_breaker_message_count_thresholds():
    assert context_budget.context_circuit_breaker(1_000, message_count=179)["stage"] == "ok"
    warn = context_budget.context_circuit_breaker(1_000, message_count=180)
    hard = context_budget.context_circuit_breaker(1_000, message_count=240)
    blocked = context_budget.context_circuit_breaker(1_000, message_count=280)

    assert warn["stage"] == "compress_before_next_turn"
    assert warn["stage_reason"] == "messages"
    assert warn["token_stage"] == "ok"
    assert warn["message_stage"] == "compress_before_next_turn"
    assert "trim_history_by_message_count" in warn["actions"]
    assert hard["stage"] == "force_fresh_session"
    assert hard["should_start_fresh_session"] is True
    assert blocked["stage"] == "block_llm"
    assert blocked["should_call_provider"] is False


def test_context_circuit_breaker_uses_strongest_token_or_message_stage():
    decision = context_budget.context_circuit_breaker(120_000, message_count=1)

    assert decision["stage"] == "block_llm"
    assert decision["stage_reason"] == "tokens"
    assert decision["token_stage"] == "block_llm"
    assert decision["message_stage"] == "ok"


def test_context_circuit_breaker_validates_threshold_order():
    with pytest.raises(ValueError, match="warn_tokens < hard_tokens < max_tokens"):
        context_budget.context_circuit_breaker(100, warn_tokens=80, hard_tokens=60, max_tokens=120)
    with pytest.raises(ValueError, match="warn_messages < hard_messages < max_messages"):
        context_budget.context_circuit_breaker(
            100,
            message_count=10,
            warn_messages=80,
            hard_messages=60,
            max_messages=120,
        )


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


def test_invalid_context_budget_inputs_are_rejected():
    with pytest.raises(TypeError, match="used_tokens must be an integer"):
        context_usage(True, 100)
    with pytest.raises(ValueError, match="used_tokens must be greater than or equal to 0"):
        context_usage(-1, 100)
    with pytest.raises(ValueError, match="process_id is required"):
        build_context_budget_event("", used_tokens=1, max_tokens=100)
    with pytest.raises(ValueError, match="max_tokens is required"):
        build_context_budget_event("proc-1", used_tokens=1)
    with pytest.raises(ValueError, match="used_tokens or text is required"):
        build_context_budget_event("proc-1", max_tokens=100)


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


def test_cli_main_estimate_text_in_process(monkeypatch, capsys):
    raw_text = "raw prompt should not be printed"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "context_budget.py",
            "estimate",
            "--max-tokens",
            "100",
            "--text",
            raw_text,
            "--process-id",
            "proc-main",
            "--source",
            "provider",
        ],
    )

    assert context_budget.main() == 0
    stdout = capsys.readouterr().out
    payload = json.loads(stdout)
    assert payload["process_id"] == "proc-main"
    assert payload["source"] == "provider"
    assert payload["used_tokens"] == estimate_tokens(raw_text)
    assert raw_text not in stdout


def test_cli_main_estimate_file_in_process(tmp_path, monkeypatch, capsys):
    raw_text = "file prompt should not be printed"
    text_file = tmp_path / "context.txt"
    text_file.write_text(raw_text, encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "context_budget.py",
            "estimate",
            "--max-tokens",
            "100",
            "--text-file",
            str(text_file),
        ],
    )

    assert context_budget.main() == 0
    stdout = capsys.readouterr().out
    assert json.loads(stdout)["used_tokens"] == estimate_tokens(raw_text)
    assert raw_text not in stdout


def test_cli_main_rejects_conflicting_or_missing_text(tmp_path, monkeypatch, capsys):
    text_file = tmp_path / "context.txt"
    text_file.write_text("context", encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "context_budget.py",
            "estimate",
            "--max-tokens",
            "100",
            "--text",
            "inline",
            "--text-file",
            str(text_file),
        ],
    )
    with pytest.raises(SystemExit) as conflict:
        context_budget.main()
    assert conflict.value.code == 2
    assert "use only one of --text or --text-file" in capsys.readouterr().err

    monkeypatch.setattr(sys, "argv", ["context_budget.py", "estimate", "--max-tokens", "100"])
    with pytest.raises(SystemExit) as missing:
        context_budget.main()
    assert missing.value.code == 2
    assert "--text or --text-file is required" in capsys.readouterr().err
