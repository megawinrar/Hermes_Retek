#!/usr/bin/env python3
"""Patch Hermes tool guardrails to force marketplace parsers through process."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


PATCH_MARKER = "HERMES_RETEK_MARKETPLACE_PROCESS_FIRST_GUARD"

IMPORT_ANCHOR = "import json\n"
IMPORT_LINE = "import os\n"

HELPER_ANCHOR = "\n\nclass ToolCallGuardrailController:\n"
HELPER_BLOCK = f'''

# {PATCH_MARKER}: marketplace/browser parser scripts must start with hermes_process.
MARKETPLACE_PROCESS_FIRST_TOOLS = frozenset({{"execute_code", "write_file", "terminal"}})
MARKETPLACE_PROCESS_FIRST_PATTERNS = (
    "b2b-center.ru",
    "zakupki.kontur.ru",
    "kontur.zakupki",
    "rebrowser-puppeteer",
    "puppeteer.launch",
    "b2b-scraper",
    "search-kontur",
    "login-kontur",
)


def _marketplace_process_first_decision(
    tool_name: str,
    args: Mapping[str, Any] | None,
    signature: ToolCallSignature,
) -> ToolGuardrailDecision | None:
    if os.environ.get("HERMES_RETEK_MARKETPLACE_PROCESS_GUARD", "1").strip().lower() in {{"0", "false", "no", "off"}}:
        return None
    if tool_name not in MARKETPLACE_PROCESS_FIRST_TOOLS:
        return None
    raw = canonical_tool_args(_coerce_args(args)).lower()
    if not any(pattern in raw for pattern in MARKETPLACE_PROCESS_FIRST_PATTERNS):
        return None
    return ToolGuardrailDecision(
        action="block_continue",
        code="marketplace_process_first_required",
        message=(
            "Marketplace/browser parsing scripts must start through "
            'hermes_process(action="run", task=...) so Bot#1/Bot#2, site policy, '
            "RLM logging, pacing, and checkpoint rules are active. Do not create or "
            "run ad-hoc Puppeteer/marketplace scraper scripts before the process exists."
        ),
        tool_name=tool_name,
        count=1,
        signature=signature,
    )
'''.rstrip()

CALL_ANCHOR = "        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))\n"
CALL_BLOCK = """        marketplace_decision = _marketplace_process_first_decision(tool_name, args, signature)
        if marketplace_decision is not None:
            if marketplace_decision.should_halt:
                self._halt_decision = marketplace_decision
            return marketplace_decision
"""


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-marketplace-process-guard-{stamp}")


def patch_marketplace_process_guard(source: str) -> tuple[str, bool]:
    """Return patched source and whether it changed."""
    if PATCH_MARKER in source and CALL_BLOCK in source:
        if 'action="block_continue"' in source:
            return source, False
        updated = source.replace(
            'action="block",\n        code="marketplace_process_first_required"',
            'action="block_continue",\n        code="marketplace_process_first_required"',
            1,
        )
        return updated, updated != source
    if PATCH_MARKER in source and 'action="block_continue"' in source:
        old_call_block = """        marketplace_decision = _marketplace_process_first_decision(tool_name, args, signature)
        if marketplace_decision is not None:
            self._halt_decision = marketplace_decision
            return marketplace_decision
"""
        if old_call_block in source:
            updated = source.replace(old_call_block, CALL_BLOCK, 1)
            return updated, updated != source
    if HELPER_ANCHOR not in source:
        raise ValueError("helper anchor not found")
    if CALL_ANCHOR not in source:
        raise ValueError("before_call anchor not found")

    updated = source
    if IMPORT_LINE not in updated:
        if IMPORT_ANCHOR not in updated:
            raise ValueError("import anchor not found")
        updated = updated.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_LINE, 1)
    updated = updated.replace(HELPER_ANCHOR, "\n\n" + HELPER_BLOCK + HELPER_ANCHOR, 1)
    updated = updated.replace(CALL_ANCHOR, CALL_ANCHOR + CALL_BLOCK, 1)
    return updated, updated != source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to agent/tool_guardrails.py")
    parser.add_argument("--no-backup", action="store_true", help="Do not write a timestamped backup")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    updated, changed = patch_marketplace_process_guard(source)
    if not changed:
        print("marketplace_process_guard=already_present")
        return 0
    if not args.no_backup:
        shutil.copy2(args.path, backup_path(args.path))
    args.path.write_text(updated, encoding="utf-8")
    print("marketplace_process_guard=applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
