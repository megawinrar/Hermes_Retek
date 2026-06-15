#!/usr/bin/env python3
"""Patch Hermes conversation loop to surface actionable provider errors."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


PATCH_MARKER = "HERMES_RETEK_PROVIDER_ERROR_GUIDANCE_PATCH"

HELPER_ANCHOR = "logger = logging.getLogger(__name__)\n\n# Stable prefix"
HELPER_BLOCK = f'''logger = logging.getLogger(__name__)

# {PATCH_MARKER}: convert known provider failures into actionable Telegram text.
def _hermes_retek_provider_error_hint(
    *,
    status_code=None,
    summary: str = "",
    body=None,
    provider: str = "",
    base_url: str = "",
    model: str = "",
    context_tokens=None,
    message_count=None,
) -> str:
    try:
        import sys as _sys
        _scripts_dir = os.environ.get("HERMES_ASSISTANT_SCRIPTS", "/opt/hermes-assistant/scripts")
        if _scripts_dir and _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from provider_error_hints import provider_error_hint as _provider_error_hint

        return _provider_error_hint(
            status_code=status_code,
            summary=summary,
            body=body,
            provider=provider,
            base_url=base_url,
            model=model,
            context_tokens=context_tokens,
            message_count=message_count,
        )
    except Exception as exc:
        logger.debug("provider error hint failed: %s", exc)
        return ""

# Stable prefix'''.rstrip()

NON_RETRYABLE_ANCHOR = '''                    return {
                        "final_response": None,
                        "messages": messages,
                        "api_calls": api_call_count,
                        "completed": False,
                        "failed": True,
                        "error": str(api_error),
                    }
'''
NON_RETRYABLE_BLOCK = '''                    _provider_hint = _hermes_retek_provider_error_hint(
                        status_code=status_code,
                        summary=agent._summarize_api_error(api_error),
                        body=getattr(api_error, "body", None),
                        provider=_provider,
                        base_url=str(_base),
                        model=_model,
                        context_tokens=approx_tokens,
                        message_count=len(api_messages),
                    )
                    return {
                        "final_response": _provider_hint or None,
                        "messages": messages,
                        "api_calls": api_call_count,
                        "completed": False,
                        "failed": True,
                        "error": str(api_error),
                    }
'''


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-provider-error-guidance-{stamp}")


def patch_provider_error_guidance(source: str) -> tuple[str, bool]:
    """Return patched source and whether it changed."""
    if PATCH_MARKER in source:
        return source, False
    if HELPER_ANCHOR not in source:
        raise ValueError("helper anchor not found")
    if NON_RETRYABLE_ANCHOR not in source:
        raise ValueError("non-retryable return anchor not found")
    updated = source.replace(HELPER_ANCHOR, HELPER_BLOCK, 1)
    updated = updated.replace(NON_RETRYABLE_ANCHOR, NON_RETRYABLE_BLOCK, 1)
    return updated, updated != source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to agent/conversation_loop.py")
    parser.add_argument("--no-backup", action="store_true", help="Do not write a timestamped backup")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    updated, changed = patch_provider_error_guidance(source)
    if not changed:
        print("provider_error_guidance=already_present")
        return 0
    if not args.no_backup:
        shutil.copy2(args.path, backup_path(args.path))
    args.path.write_text(updated, encoding="utf-8")
    print("provider_error_guidance=applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
