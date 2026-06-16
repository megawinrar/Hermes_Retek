#!/usr/bin/env python3
"""Patch Hermes conversation loop with a pre-provider context guard."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


PATCH_MARKER = "HERMES_RETEK_PRE_API_CONTEXT_GUARD"

HELPER_ANCHOR = "def _ollama_context_limit_error(agent: Any, request_tokens: int) -> Optional[str]:\n"
HELPER_BLOCK = f'''# {PATCH_MARKER}: do not spend provider calls on already oversized requests.
def _hermes_retek_int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default)).strip()))
    except (TypeError, ValueError):
        return default


def _hermes_retek_pre_api_context_guard(
    agent,
    *,
    approx_tokens: int,
    approx_request_tokens: int,
    message_count: int,
) -> dict:
    if os.environ.get("HERMES_PRE_API_CONTEXT_GUARD_ENABLED", "1").strip().lower() in {{"0", "false", "no", "off"}}:
        return {{}}
    try:
        import sys as _sys
        _scripts_dir = os.environ.get("HERMES_ASSISTANT_SCRIPTS", "/opt/hermes-assistant/scripts")
        if _scripts_dir and _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from context_budget import context_circuit_breaker as _context_circuit_breaker

        guard_tokens = max(int(approx_tokens or 0), int(approx_request_tokens or 0))
        decision = _context_circuit_breaker(
            guard_tokens,
            warn_tokens=_hermes_retek_int_env("HERMES_CONTEXT_WARN_TOKENS", 60000),
            hard_tokens=_hermes_retek_int_env("HERMES_CONTEXT_HARD_TOKENS", 80000),
            max_tokens=_hermes_retek_int_env("HERMES_CONTEXT_MAX_TOKENS", 120000),
            message_count=int(message_count or 0),
            warn_messages=_hermes_retek_int_env("HERMES_CONTEXT_WARN_MESSAGES", 180),
            hard_messages=_hermes_retek_int_env("HERMES_CONTEXT_HARD_MESSAGES", 240),
            max_messages=_hermes_retek_int_env("HERMES_CONTEXT_MAX_MESSAGES", 280),
        )
        logger.info(
            "pre-api context guard probe: stage=%s reason=%s tokens=%s messages=%s session=%s",
            decision.get("stage"),
            decision.get("stage_reason"),
            decision.get("used_tokens"),
            decision.get("message_count"),
            getattr(agent, "session_id", None) or "none",
        )
        return decision
    except Exception as exc:
        logger.warning("pre-api context guard failed open: %s", exc)
        return {{}}


def _ollama_context_limit_error(agent: Any, request_tokens: int) -> Optional[str]:
'''

CALL_ANCHOR = '''        max_compression_attempts = 3

        finish_reason = "stop"
'''
CALL_BLOCK = '''        max_compression_attempts = 3

        # ── Retek pre-API context guard ──
        _hermes_retek_api_context_decision = _hermes_retek_pre_api_context_guard(
            agent,
            approx_tokens=approx_tokens,
            approx_request_tokens=approx_request_tokens,
            message_count=len(api_messages),
        )
        if _hermes_retek_api_context_decision:
            _guard_stage = str(_hermes_retek_api_context_decision.get("stage") or "ok")
            _guard_tokens = int(_hermes_retek_api_context_decision.get("used_tokens") or 0)
            _guard_messages = int(_hermes_retek_api_context_decision.get("message_count") or 0)
            if _guard_stage in {"force_fresh_session", "block_llm"}:
                logger.warning(
                    "pre-api context guard action: stage=%s tokens=%s messages=%s api_call=%s session=%s",
                    _guard_stage,
                    _guard_tokens,
                    _guard_messages,
                    api_call_count,
                    agent.session_id or "none",
                )
                if agent.compression_enabled and compression_attempts < max_compression_attempts:
                    compression_attempts += 1
                    agent._emit_status(
                        f"🧠 Context is large before provider call "
                        f"(~{_guard_tokens:,} tokens, {_guard_messages} messages). "
                        f"Compressing/checkpointing first ({compression_attempts}/{max_compression_attempts})."
                    )
                    _orig_len = len(messages)
                    messages, active_system_prompt = agent._compress_context(
                        messages,
                        system_message,
                        approx_tokens=_guard_tokens,
                        task_id=effective_task_id,
                    )
                    conversation_history = None
                    agent._empty_content_retries = 0
                    agent._thinking_prefill_retries = 0
                    agent._last_content_with_tools = None
                    agent._last_content_tools_all_housekeeping = False
                    agent._mute_post_response = False
                    if len(messages) < _orig_len:
                        agent._emit_status(
                            f"🗜️ Compressed context {_orig_len} → {len(messages)} messages; retrying provider call."
                        )
                        api_call_count = max(0, api_call_count - 1)
                        agent._api_call_count = api_call_count
                        try:
                            agent.iteration_budget.refund()
                        except Exception:
                            pass
                        continue

                if not bool(_hermes_retek_api_context_decision.get("should_call_provider", True)):
                    api_call_count = max(0, api_call_count - 1)
                    agent._api_call_count = api_call_count
                    try:
                        agent.iteration_budget.refund()
                    except Exception:
                        pass
                    final_response = (
                        "Контекст этой Telegram-сессии слишком большой для надежного LLM-запроса "
                        f"(~{_guard_tokens:,} tokens, {_guard_messages} messages), а сжатие не уменьшило его.\\n\\n"
                        "Я не отправляю такой запрос в BotHub, чтобы не сжигать лимит и не ловить таймаут. "
                        "Результаты парсеров и уроки должны быть сохранены в файлах/RLM; продолжай задачу коротким новым сообщением "
                        "или через новый процесс, опираясь на последние артефакты."
                    )
                    messages.append({"role": "assistant", "content": final_response})
                    try:
                        agent._persist_session(messages, conversation_history)
                    except Exception:
                        pass
                    return {
                        "final_response": final_response,
                        "messages": messages,
                        "api_calls": api_call_count,
                        "completed": False,
                        "failed": True,
                        "error": "pre_api_context_guard_blocked",
                        "compression_exhausted": True,
                    }

        finish_reason = "stop"
'''


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-pre-api-context-guard-{stamp}")


def patch_pre_api_context_guard(source: str) -> tuple[str, bool]:
    """Return patched source and whether it changed."""
    if PATCH_MARKER in source:
        return source, False
    if HELPER_ANCHOR not in source:
        raise ValueError("helper anchor not found")
    if CALL_ANCHOR not in source:
        raise ValueError("pre-api call anchor not found")
    updated = source.replace(HELPER_ANCHOR, HELPER_BLOCK, 1)
    updated = updated.replace(CALL_ANCHOR, CALL_BLOCK, 1)
    return updated, updated != source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to agent/conversation_loop.py")
    parser.add_argument("--no-backup", action="store_true", help="Do not write a timestamped backup")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    updated, changed = patch_pre_api_context_guard(source)
    if not changed:
        print("pre_api_context_guard=already_present")
        return 0
    if not args.no_backup:
        shutil.copy2(args.path, backup_path(args.path))
    args.path.write_text(updated, encoding="utf-8")
    print("pre_api_context_guard=applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
