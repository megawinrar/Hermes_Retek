#!/usr/bin/env python3
"""Structured JSONL process logging for Hermes runtime glue."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from secret_patterns import redact_payload
except ImportError:  # pragma: no cover
    from scripts.secret_patterns import redact_payload


DEFAULT_LOG_PATH = Path("/opt/data/logs/hermes_process_events.jsonl")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def log_path() -> Path:
    return Path(os.environ.get("HERMES_PROCESS_LOG_PATH", str(DEFAULT_LOG_PATH)))


def log_event(
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    process_id: str = "",
    level: str = "info",
    path: Path | str | None = None,
) -> dict[str, Any]:
    """Append one redacted JSONL event and never raise into the caller."""
    target = Path(path) if path is not None else log_path()
    event = {
        "ts": utc_now(),
        "level": str(level or "info"),
        "event_type": str(event_type or "event"),
        "process_id": str(process_id or ""),
        "payload": redact_payload(payload or {}),
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        return {"ok": True, "path": str(target)}
    except Exception as exc:  # pragma: no cover - log path must never break Hermes
        return {"ok": False, "path": str(target), "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("event_type")
    parser.add_argument("--process-id", default="")
    parser.add_argument("--level", default="info")
    parser.add_argument("--payload-json", default="{}")
    parser.add_argument("--path", default="")
    args = parser.parse_args()
    payload = json.loads(args.payload_json)
    result = log_event(
        args.event_type,
        payload,
        process_id=args.process_id,
        level=args.level,
        path=args.path or None,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
