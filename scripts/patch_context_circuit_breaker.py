#!/usr/bin/env python3
"""Patch Hermes turn prologue with a context circuit breaker."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


PATCH_MARKER = "HERMES_RETEK_CONTEXT_CIRCUIT_BREAKER_PATCH"

IMPORT_ANCHOR = "import logging\n"
IMPORT_BLOCK = "import logging\nimport os\n"

HELPER_ANCHOR = "logger = logging.getLogger(__name__)\n\n\n@dataclass"
HELPER_BLOCK = f'''logger = logging.getLogger(__name__)


# {PATCH_MARKER}: keep huge Telegram sessions from blindly walking into provider failure.
def _hermes_retek_int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default)).strip()))
    except (TypeError, ValueError):
        return default


def _hermes_retek_context_breaker(agent, messages, active_system_prompt):
    if os.environ.get("HERMES_CONTEXT_CIRCUIT_BREAKER_ENABLED", "1").strip().lower() in {{"0", "false", "no", "off"}}:
        return None
    try:
        import sys as _sys
        _scripts_dir = os.environ.get("HERMES_ASSISTANT_SCRIPTS", "/opt/hermes-assistant/scripts")
        if _scripts_dir and _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from context_budget import context_circuit_breaker as _context_circuit_breaker

        tokens = estimate_request_tokens_rough(
            messages,
            system_prompt=active_system_prompt or "",
            tools=agent.tools or None,
        )
        return _context_circuit_breaker(
            tokens,
            warn_tokens=_hermes_retek_int_env("HERMES_CONTEXT_WARN_TOKENS", 60000),
            hard_tokens=_hermes_retek_int_env("HERMES_CONTEXT_HARD_TOKENS", 80000),
            max_tokens=_hermes_retek_int_env("HERMES_CONTEXT_MAX_TOKENS", 120000),
        )
    except Exception as exc:
        logger.debug("context circuit breaker failed: %s", exc)
        return None


@dataclass'''.rstrip()

CALL_ANCHOR = '''    # ── Preflight context compression ──
    if (
'''
CALL_BLOCK = '''    # ── Retek context circuit breaker ──
    _hermes_retek_context_decision = _hermes_retek_context_breaker(agent, messages, active_system_prompt)
    if _hermes_retek_context_decision:
        _stage = str(_hermes_retek_context_decision.get("stage") or "ok")
        _tokens = int(_hermes_retek_context_decision.get("used_tokens") or 0)
        if _stage == "compress_before_next_turn":
            logger.warning(
                "context circuit breaker warning: stage=%s tokens=%s session=%s",
                _stage, _tokens, agent.session_id or "none",
            )
            agent._emit_status(
                f"🧠 Context is getting large (~{_tokens:,} tokens). "
                "I will checkpoint useful parser lessons and keep the next steps tighter."
            )
        elif _stage in {"force_fresh_session", "block_llm"}:
            logger.warning(
                "context circuit breaker hard action: stage=%s tokens=%s session=%s",
                _stage, _tokens, agent.session_id or "none",
            )
            agent._emit_status(
                f"🧠 Context is too large (~{_tokens:,} tokens). "
                "Compressing before the next model call; large parser results should go to RLM/files, not chat."
            )
            if agent.compression_enabled:
                for _pass in range(3):
                    _orig_len = len(messages)
                    messages, active_system_prompt = agent._compress_context(
                        messages, system_message, approx_tokens=_tokens,
                        task_id=effective_task_id,
                    )
                    conversation_history = None
                    if len(messages) >= _orig_len:
                        break
                    _tokens = estimate_request_tokens_rough(
                        messages,
                        system_prompt=active_system_prompt or "",
                        tools=agent.tools or None,
                    )
                    if _tokens < int(_hermes_retek_context_decision.get("hard_tokens") or 80000):
                        break

    # ── Preflight context compression ──
    if (
'''


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-context-circuit-breaker-{stamp}")


def patch_context_circuit_breaker(source: str) -> tuple[str, bool]:
    """Return patched source and whether it changed."""
    if PATCH_MARKER in source:
        return source, False
    updated = source
    if "import os\n" not in updated:
        if IMPORT_ANCHOR not in updated:
            raise ValueError("import anchor not found")
        updated = updated.replace(IMPORT_ANCHOR, IMPORT_BLOCK, 1)
    if HELPER_ANCHOR not in updated:
        raise ValueError("helper anchor not found")
    if CALL_ANCHOR not in updated:
        raise ValueError("preflight anchor not found")
    updated = updated.replace(HELPER_ANCHOR, HELPER_BLOCK, 1)
    updated = updated.replace(CALL_ANCHOR, CALL_BLOCK, 1)
    return updated, updated != source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to agent/turn_context.py")
    parser.add_argument("--no-backup", action="store_true", help="Do not write a timestamped backup")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    updated, changed = patch_context_circuit_breaker(source)
    if not changed:
        print("context_circuit_breaker=already_present")
        return 0
    if not args.no_backup:
        shutil.copy2(args.path, backup_path(args.path))
    args.path.write_text(updated, encoding="utf-8")
    print("context_circuit_breaker=applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
