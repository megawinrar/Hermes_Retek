#!/usr/bin/env python3
"""Patch Hermes toolsets so hermes_process is exposed to default agents."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


PATCH_MARKER = "HERMES_RETEK_HERMES_PROCESS_TOOLSET_PATCH"
CORE_ANCHOR = '    "terminal", "process",\n'
CORE_REPLACEMENT = '    "terminal", "process", "hermes_process",\n'
TERMINAL_ANCHOR = '        "tools": ["terminal", "process"],\n'
TERMINAL_REPLACEMENT = '        "tools": ["terminal", "process", "hermes_process"],\n'


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-hermes-process-toolset-{stamp}")


def patch_hermes_process_toolset(source: str) -> tuple[str, bool]:
    """Return patched source and whether it changed."""
    if PATCH_MARKER in source and "hermes_process" in source:
        return source, False
    if CORE_ANCHOR not in source and CORE_REPLACEMENT not in source:
        raise ValueError("core tools anchor not found")
    if TERMINAL_ANCHOR not in source and TERMINAL_REPLACEMENT not in source:
        raise ValueError("terminal toolset anchor not found")

    updated = source
    if CORE_REPLACEMENT not in updated:
        updated = updated.replace(CORE_ANCHOR, CORE_REPLACEMENT, 1)
    if TERMINAL_REPLACEMENT not in updated:
        updated = updated.replace(TERMINAL_ANCHOR, TERMINAL_REPLACEMENT, 1)

    if PATCH_MARKER not in updated:
        updated = updated.replace(
            CORE_REPLACEMENT,
            f"    # {PATCH_MARKER}: expose Retek process supervisor to Telegram/default tool schemas.\n"
            f"{CORE_REPLACEMENT}",
            1,
        )
    return updated, updated != source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to Hermes toolsets.py")
    parser.add_argument("--no-backup", action="store_true", help="Do not write a timestamped backup")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    updated, changed = patch_hermes_process_toolset(source)
    if not changed:
        print("hermes_process_toolset=already_present")
        return 0
    if not args.no_backup:
        shutil.copy2(args.path, backup_path(args.path))
    args.path.write_text(updated, encoding="utf-8")
    print("hermes_process_toolset=applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
