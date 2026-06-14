from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from patch_telegram_supervisor_buttons import CALLBACK_MARKER, HELPER_MARKER, patch_text  # noqa: E402


def test_patch_text_installs_supervisor_button_handler_once() -> None:
    source = """
class TelegramAdapter:
    async def _handle_callback_query(
        self, update, context
    ) -> None:
        data = update.callback_query.data

        # --- Update prompt callbacks ---
        if not data.startswith("update_prompt:"):
            return
"""

    patched, changes = patch_text(source)
    assert changes == ["helper_methods", "callback_branch"]
    assert HELPER_MARKER in patched
    assert CALLBACK_MARKER in patched

    second, second_changes = patch_text(patched)
    assert second_changes == []
    assert second == patched
