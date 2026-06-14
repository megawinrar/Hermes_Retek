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


def test_patch_text_upgrades_existing_button_handler() -> None:
    source = """
class TelegramAdapter:
    def _hermes_process_cli_base(self) -> list[str]:
        return []

    async def _run_hermes_process_callback(self, action: str, process_id: str, *, choice: str = "", reason: str = "") -> dict:
        if action == "decide":
            return {"ok": True}
        return {"ok": False}

    async def _handle_callback_query(
        self, update, context
    ) -> None:
        data = update.callback_query.data

        # --- Hermes process supervisor callbacks (hp:action:process_id) ---
        if data.startswith("hp:"):
            return

        # --- Update prompt callbacks ---
        if not data.startswith("update_prompt:"):
            return
"""

    patched, changes = patch_text(source)

    assert changes == ["helper_methods_upgrade", "callback_branch_upgrade"]
    assert '"continue"' in patched
    assert "continue_result" in patched
    assert "Auto-continue after YES" in patched

    second, second_changes = patch_text(patched)
    assert second_changes == []
    assert second == patched
