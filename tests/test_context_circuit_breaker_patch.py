from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import patch_context_circuit_breaker  # noqa: E402


BASE_SNIPPET = '''import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TurnContext:
    messages: list

def build_turn_context(agent, messages, active_system_prompt, system_message, effective_task_id):
    try:
        agent._persist_session(messages, conversation_history)
    except Exception:
        pass

    # ── Preflight context compression ──
    if (
        agent.compression_enabled
        and len(messages) > 10
    ):
        pass
'''


def test_patch_context_circuit_breaker_inserts_helper_and_preflight_call() -> None:
    updated, changed = patch_context_circuit_breaker.patch_context_circuit_breaker(BASE_SNIPPET)

    assert changed is True
    assert patch_context_circuit_breaker.PATCH_MARKER in updated
    assert "import os" in updated
    assert "def _hermes_retek_context_breaker(" in updated
    assert "from context_budget import context_circuit_breaker" in updated
    assert "message_count=len(messages or [])" in updated
    assert "context circuit breaker probe" in updated
    assert "Retek context circuit breaker" in updated
    assert "large parser results should go to RLM/files" in updated
    assert updated.index("Retek context circuit breaker") < updated.index("Preflight context compression")


def test_patch_context_circuit_breaker_is_idempotent() -> None:
    updated, changed = patch_context_circuit_breaker.patch_context_circuit_breaker(BASE_SNIPPET)
    second, changed_again = patch_context_circuit_breaker.patch_context_circuit_breaker(updated)

    assert changed is True
    assert changed_again is False
    assert second == updated
    assert second.count(patch_context_circuit_breaker.PATCH_MARKER) == 1


def test_patch_context_circuit_breaker_upgrades_old_marker() -> None:
    old, changed = patch_context_circuit_breaker.patch_context_circuit_breaker(BASE_SNIPPET)
    old = old.replace(
        patch_context_circuit_breaker.PATCH_MARKER,
        patch_context_circuit_breaker.OLD_PATCH_MARKER,
    ).replace("message_count=len(messages or []),", "")

    upgraded, upgraded_changed = patch_context_circuit_breaker.patch_context_circuit_breaker(old)

    assert changed is True
    assert upgraded_changed is True
    assert patch_context_circuit_breaker.PATCH_MARKER in upgraded
    assert patch_context_circuit_breaker.OLD_PATCH_MARKER in upgraded
    assert "message_count=len(messages or [])" in upgraded
    assert "context circuit breaker probe" in upgraded
    assert upgraded.count("Preflight context compression") == 1
    assert "if (\n    # ── Preflight context compression ──" not in upgraded
