"""Tests for secret_patterns redaction (top-15 #10).

These primitives scrub secrets from every human-gate Telegram message and report,
so they need a direct assertion that they actually replace.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from secret_patterns import redact_payload, redact_text  # noqa: E402


PRIVATE_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\nabc123def456\n-----END OPENSSH PRIVATE KEY-----"
)


@pytest.mark.parametrize(
    "secret",
    [
        "github_pat_" + "A" * 22,
        "ghp_" + "B" * 22,
        "glpat-" + "C" * 22,
        PRIVATE_KEY,
    ],
)
def test_redact_text_replaces_known_secret_shapes(secret: str) -> None:
    out = redact_text(f"before {secret} after")
    assert secret not in out
    assert "[REDACTED]" in out


def test_redact_text_leaves_clean_text_untouched() -> None:
    text = "just a normal sentence with no secrets"
    assert redact_text(text) == text


def test_redact_payload_recurses_and_coerces_tuple_to_list() -> None:
    payload = {
        "token": "github_pat_" + "Z" * 22,
        "nested": ["ghp_" + "Y" * 22, "fine"],
        "pair": ("glpat-" + "X" * 22, "ok"),
        "count": 3,
    }
    out = redact_payload(payload)
    assert out["token"] == "[REDACTED]"
    assert out["nested"][0] == "[REDACTED]"
    assert out["nested"][1] == "fine"
    assert isinstance(out["pair"], list)  # tuple -> list
    assert out["pair"][0] == "[REDACTED]"
    assert out["pair"][1] == "ok"
    assert out["count"] == 3
