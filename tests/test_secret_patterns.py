from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from secret_patterns import redact_payload, redact_text  # noqa: E402


def test_redact_payload_handles_tuple_values_and_cookie_assignments() -> None:
    bearer = "Authorization: Bearer " + "J" * 32
    cookie = "auth.sid=" + "K" * 32

    redacted = redact_payload(("safe", bearer, {"cookie": cookie}))

    assert redacted == ["safe", "[REDACTED]", {"cookie": "[REDACTED]"}]


def test_cookie_redaction_does_not_flag_code_like_session_id_assignment() -> None:
    assert redact_text("session_id=process_id_value") == "session_id=process_id_value"
    assert redact_text('session_id="S' + "L" * 32 + '"') == "[REDACTED]"
