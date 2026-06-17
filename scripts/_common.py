#!/usr/bin/env python3
"""Tiny dependency-free primitives shared across Hermes Retek scripts.

Intentionally importable from every other script without pulling in SQLite,
subprocess, or network code. Holds helpers that were previously copy-pasted
verbatim into 6-8 modules (timestamp, prefixed id generator, .env reader).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    """UTC timestamp truncated to seconds, e.g. ``2026-06-18T12:00:00+00:00``."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def gen_id(prefix: str) -> str:
    """Sortable, collision-resistant id: ``<prefix>-<YYYYMMDD-HHMMSS>-<6 hex>``."""
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env``-style file into a dict, ignoring blanks and comments.

    Returns an empty dict if the file does not exist. Values are stripped of
    surrounding single or double quotes. This is the body that previously lived
    duplicated in dual_bot_lab.load_env_file and bot2_gate.load_env.
    """
    data: dict[str, str] = {}
    if not path.exists():
        return data
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data
