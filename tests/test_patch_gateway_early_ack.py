from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import patch_gateway_early_ack  # noqa: E402


BASE_SNIPPET = '''    async def _process_message_background(self, event: MessageEvent, session_key: str) -> None:
        """Background task that actually processes the message."""
        delivery_attempted = False
        delivery_succeeded = False
        _thread_metadata = _thread_metadata_for_source(event.source, _reply_anchor_for_event(event))
        _keep_typing_kwargs = {"metadata": _thread_metadata}
        typing_task = asyncio.create_task(
            self._keep_typing(
                event.source.chat_id,
                **_keep_typing_kwargs,
            )
        )

        async def _stop_typing_task() -> None:
            pass

        try:
            await self._run_processing_hook("on_processing_start", event)

            response = await self._message_handler(event)
'''


def test_patch_gateway_early_ack_inserts_helper_and_call() -> None:
    updated, changed = patch_gateway_early_ack.patch_gateway_early_ack(BASE_SNIPPET)

    assert changed is True
    assert patch_gateway_early_ack.PATCH_MARKER in updated
    assert 'source_platform != "telegram"' in updated
    assert 'HERMES_TELEGRAM_EARLY_ACK_ENABLED' in updated
    assert 'await _send_early_ack()' in updated
    assert updated.index('await self._run_processing_hook("on_processing_start", event)') < updated.index(
        "await _send_early_ack()"
    )
    assert updated.index("await _send_early_ack()") < updated.index("response = await self._message_handler(event)")


def test_patch_gateway_early_ack_is_idempotent() -> None:
    updated, changed = patch_gateway_early_ack.patch_gateway_early_ack(BASE_SNIPPET)
    second, changed_again = patch_gateway_early_ack.patch_gateway_early_ack(updated)

    assert changed is True
    assert changed_again is False
    assert second == updated
    assert second.count(patch_gateway_early_ack.PATCH_MARKER) == 1
