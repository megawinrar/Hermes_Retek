from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import patch_pre_api_context_guard  # noqa: E402


BASE_SNIPPET = '''import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

def _ollama_context_limit_error(agent: Any, request_tokens: int) -> Optional[str]:
    """Return a user-facing error when Ollama is loaded with too little context."""
    return None

def run(agent):
        api_call_count = 1
        approx_tokens = 184000
        approx_request_tokens = 190000
        api_messages = [{"role": "user", "content": "x"}]
        messages = []
        active_system_prompt = ""
        system_message = ""
        effective_task_id = "task"
        conversation_history = []
        compression_attempts = 0
        max_compression_attempts = 3

        finish_reason = "stop"
        return finish_reason
'''


def test_patch_pre_api_context_guard_inserts_helper_and_call() -> None:
    updated, changed = patch_pre_api_context_guard.patch_pre_api_context_guard(BASE_SNIPPET)

    assert changed is True
    assert patch_pre_api_context_guard.PATCH_MARKER in updated
    assert "def _hermes_retek_pre_api_context_guard(" in updated
    assert "from context_budget import context_circuit_breaker" in updated
    assert "pre-api context guard probe" in updated
    assert "pre-api context guard action" in updated
    assert "do not spend provider calls" in updated
    assert "pre_api_context_guard_blocked" in updated
    assert 'def _ollama_context_limit_error(agent: Any, request_tokens: int) -> Optional[str]:\n    """' in updated
    assert updated.index("pre-API context guard") < updated.index('finish_reason = "stop"')


def test_patch_pre_api_context_guard_is_idempotent() -> None:
    updated, changed = patch_pre_api_context_guard.patch_pre_api_context_guard(BASE_SNIPPET)
    second, changed_again = patch_pre_api_context_guard.patch_pre_api_context_guard(updated)

    assert changed is True
    assert changed_again is False
    assert second == updated
    assert second.count(patch_pre_api_context_guard.PATCH_MARKER) == 1
