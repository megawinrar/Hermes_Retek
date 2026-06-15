#!/usr/bin/env python3
"""Patch Hermes gateway runtime to send an early Telegram ack before LLM work."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


PATCH_MARKER = "HERMES_RETEK_EARLY_ACK_PATCH"

HELPER_BLOCK = f'''
        # {PATCH_MARKER}: send a visible ack before the first LLM/tool loop.
        async def _send_early_ack() -> None:
            if os.environ.get("HERMES_TELEGRAM_EARLY_ACK_ENABLED", "1").strip().lower() in {{"0", "false", "no", "off"}}:
                return
            source_platform = _platform_name(getattr(event.source, "platform", None))
            if source_platform != "telegram":
                return
            text = os.environ.get("HERMES_TELEGRAM_EARLY_ACK_TEXT", "Принял, работаю.").strip()
            if not text:
                return
            try:
                metadata = dict(_thread_metadata or {{}})
                metadata.setdefault("notify", False)
                await self._send_with_retry(
                    chat_id=event.source.chat_id,
                    content=text,
                    reply_to=_reply_anchor_for_event(event),
                    metadata=metadata,
                    max_retries=0,
                    base_delay=0.1,
                )
                logger.info("[%s] early ack sent to %s", self.name, event.source.chat_id)
            except Exception as exc:
                logger.debug("[%s] early ack failed for %s: %s", self.name, event.source.chat_id, exc)
'''.rstrip()

HELPER_ANCHOR = """        typing_task = asyncio.create_task(
            self._keep_typing(
                event.source.chat_id,
                **_keep_typing_kwargs,
            )
        )
"""

CALL_ANCHOR = '            await self._run_processing_hook("on_processing_start", event)\n'
CALL_LINE = "            await _send_early_ack()\n"


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-early-ack-{stamp}")


def patch_gateway_early_ack(source: str) -> tuple[str, bool]:
    """Return patched source and whether it changed."""
    if PATCH_MARKER in source and CALL_LINE in source:
        return source, False
    if HELPER_ANCHOR not in source:
        raise ValueError("helper anchor not found")
    if CALL_ANCHOR not in source:
        raise ValueError("call anchor not found")

    updated = source.replace(HELPER_ANCHOR, HELPER_ANCHOR + "\n" + HELPER_BLOCK + "\n", 1)
    updated = updated.replace(CALL_ANCHOR, CALL_ANCHOR + CALL_LINE, 1)
    return updated, updated != source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to gateway/platforms/base.py")
    parser.add_argument("--no-backup", action="store_true", help="Do not write a timestamped backup")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    updated, changed = patch_gateway_early_ack(source)
    if not changed:
        print("early_ack_patch=already_present")
        return 0
    if not args.no_backup:
        shutil.copy2(args.path, backup_path(args.path))
    args.path.write_text(updated, encoding="utf-8")
    print("early_ack_patch=applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
