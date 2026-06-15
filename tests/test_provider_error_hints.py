from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import provider_error_hints  # noqa: E402


def test_bothub_not_enough_tokens_gets_actionable_message() -> None:
    hint = provider_error_hints.provider_error_hint(
        status_code=403,
        summary="HTTP 403: Balance is below zero",
        body={"code": "NOT_ENOUGH_TOKENS"},
        provider="custom",
        base_url="https://openai.bothub.chat/v1",
        model="deepseek-v4-flash",
        context_tokens=184_000,
        message_count=284,
    )

    assert "BotHub" in hint
    assert "NOT_ENOUGH_TOKENS" in hint
    assert "баланс ниже нуля" in hint
    assert "deepseek-v4-flash" in hint
    assert "184,000 tokens" in hint
    assert "свежую сессию" in hint


def test_generic_403_still_has_provider_details() -> None:
    hint = provider_error_hints.provider_error_hint(
        status_code=403,
        summary="Forbidden",
        provider="custom",
        base_url="https://example.test/v1",
        model="m",
    )

    assert "HTTP 403" in hint
    assert "example.test" in hint
    assert "Forbidden" in hint


def test_unknown_error_returns_empty_hint() -> None:
    assert provider_error_hints.provider_error_hint(status_code=500, summary="server error") == ""


def test_cli_prints_json_free_hint() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/provider_error_hints.py"),
            "--status-code",
            "403",
            "--summary",
            "Balance is below zero",
            "--body",
            json.dumps({"code": "NOT_ENOUGH_TOKENS"}),
            "--base-url",
            "https://openai.bothub.chat/v1",
            "--model",
            "deepseek-v4-flash",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "BotHub" in result.stdout
    assert "NOT_ENOUGH_TOKENS" in result.stdout
