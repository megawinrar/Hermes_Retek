from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import patch_provider_error_guidance  # noqa: E402


BASE_SNIPPET = '''import os
import logging

logger = logging.getLogger(__name__)

# Stable prefix of the local interrupt status string emitted when a turn is
def run():
                    return {
                        "final_response": None,
                        "messages": messages,
                        "api_calls": api_call_count,
                        "completed": False,
                        "failed": True,
                        "error": str(api_error),
                    }
'''


def test_patch_provider_error_guidance_inserts_hint() -> None:
    updated, changed = patch_provider_error_guidance.patch_provider_error_guidance(BASE_SNIPPET)

    assert changed is True
    assert patch_provider_error_guidance.PATCH_MARKER in updated
    assert "def _hermes_retek_provider_error_hint(" in updated
    assert "from provider_error_hints import provider_error_hint" in updated
    assert '"final_response": _provider_hint or None' in updated
    assert "context_tokens=approx_tokens" in updated


def test_patch_provider_error_guidance_is_idempotent() -> None:
    updated, changed = patch_provider_error_guidance.patch_provider_error_guidance(BASE_SNIPPET)
    second, changed_again = patch_provider_error_guidance.patch_provider_error_guidance(updated)

    assert changed is True
    assert changed_again is False
    assert second == updated
    assert second.count(patch_provider_error_guidance.PATCH_MARKER) == 1
