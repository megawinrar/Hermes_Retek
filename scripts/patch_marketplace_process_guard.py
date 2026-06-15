#!/usr/bin/env python3
"""Patch Hermes tool guardrails to force marketplace parsers through process."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


PATCH_MARKER = "HERMES_RETEK_MARKETPLACE_PROCESS_FIRST_GUARD"
APPROVAL_MARKER = "HERMES_RETEK_MARKETPLACE_PROCESS_APPROVAL_GATE"

IMPORT_ANCHOR = "import json\n"
REQUIRED_IMPORTS = ("import os\n", "import sqlite3\n")

HELPER_ANCHOR = "\n\nclass ToolCallGuardrailController:\n"
HELPER_BLOCK = f'''

# {PATCH_MARKER}: marketplace/browser parser scripts must start with hermes_process.
# {APPROVAL_MARKER}: approved parser processes can unlock the matching tool calls.
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
MARKETPLACE_PROCESS_APPROVED_STATUSES = ("approved", "accepted_by_user_override")


def _marketplace_process_first_int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default)).strip()))
    except (TypeError, ValueError):
        return default


def _marketplace_process_first_store_path() -> str:
    return (
        os.environ.get("HERMES_PROCESS_STORE")
        or os.environ.get("PROCESS_STORE_PATH")
        or "/opt/data/process_orchestrator_store.db"
    )


def _marketplace_process_first_tokens(text: str) -> set[str]:
    lower = text.lower()
    tokens: set[str] = set()
    if "b2b-center" in lower or "b2b" in lower:
        tokens.add("b2b")
    if "kontur" in lower or "zakupki" in lower or "контур" in lower or "закупк" in lower:
        tokens.add("kontur")
    if "puppeteer" in lower or "rebrowser" in lower or "browser" in lower or "брауз" in lower:
        tokens.add("browser")
    if "marketplace" in lower or "площадк" in lower or "маркетплейс" in lower:
        tokens.add("marketplace")
    return tokens


def _marketplace_process_first_has_recent_approval(raw: str) -> bool:
    ttl_seconds = _marketplace_process_first_int_env(
        "HERMES_RETEK_MARKETPLACE_PROCESS_APPROVAL_TTL_SECONDS",
        1800,
    )
    store_path = _marketplace_process_first_store_path()
    raw_tokens = _marketplace_process_first_tokens(raw)
    if not raw_tokens or not store_path or not os.path.exists(store_path):
        return False
    try:
        con = sqlite3.connect(f"file:{{store_path}}?mode=ro", uri=True, timeout=0.25)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT task
            FROM process_runs
            WHERE status IN (?, ?)
              AND julianday(updated_at) >= julianday('now', ?)
            ORDER BY updated_at DESC
            LIMIT 20
            """,
            (
                MARKETPLACE_PROCESS_APPROVED_STATUSES[0],
                MARKETPLACE_PROCESS_APPROVED_STATUSES[1],
                f"-{{ttl_seconds}} seconds",
            ),
        ).fetchall()
    except Exception:
        return False
    finally:
        try:
            con.close()
        except Exception:
            pass
    return any(raw_tokens & _marketplace_process_first_tokens(str(row["task"])) for row in rows)


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
    if _marketplace_process_first_has_recent_approval(raw):
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


def ensure_imports(source: str) -> str:
    updated = source
    for import_line in reversed(REQUIRED_IMPORTS):
        if import_line in updated:
            continue
        if IMPORT_ANCHOR not in updated:
            raise ValueError("import anchor not found")
        updated = updated.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + import_line, 1)
    return updated


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-marketplace-process-guard-{stamp}")


def patch_marketplace_process_guard(source: str) -> tuple[str, bool]:
    """Return patched source and whether it changed."""
    if PATCH_MARKER in source and APPROVAL_MARKER not in source:
        if HELPER_ANCHOR not in source:
            raise ValueError("helper anchor not found")
        start = source.index(f"\n# {PATCH_MARKER}")
        end = source.index(HELPER_ANCHOR)
        updated = source[:start] + "\n" + HELPER_BLOCK + source[end:]
        updated = ensure_imports(updated)
        return updated, updated != source

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

    updated = ensure_imports(source)
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
