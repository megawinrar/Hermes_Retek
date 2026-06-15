from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import patch_hermes_process_toolset  # noqa: E402


BASE_SNIPPET = '''_HERMES_CORE_TOOLS = [
    # Web
    "web_search", "web_extract",
    # Terminal + process management
    "terminal", "process",
    # File manipulation
    "read_file", "write_file",
]

TOOLSETS = {
    "terminal": {
        "description": "Terminal/command execution and process management tools",
        "tools": ["terminal", "process"],
        "includes": []
    },
}
'''


def test_patch_hermes_process_toolset_exposes_process_tool() -> None:
    updated, changed = patch_hermes_process_toolset.patch_hermes_process_toolset(BASE_SNIPPET)

    assert changed is True
    assert patch_hermes_process_toolset.PATCH_MARKER in updated
    assert '"terminal", "process", "hermes_process"' in updated
    assert '"tools": ["terminal", "process", "hermes_process"]' in updated


def test_patch_hermes_process_toolset_is_idempotent() -> None:
    updated, changed = patch_hermes_process_toolset.patch_hermes_process_toolset(BASE_SNIPPET)
    second, changed_again = patch_hermes_process_toolset.patch_hermes_process_toolset(updated)

    assert changed is True
    assert changed_again is False
    assert second == updated
    assert second.count(patch_hermes_process_toolset.PATCH_MARKER) == 1
