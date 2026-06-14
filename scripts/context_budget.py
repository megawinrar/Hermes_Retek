#!/usr/bin/env python3
"""Context budget helper for Hermes compaction policy."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


THRESHOLD_ACTIONS: tuple[tuple[int, str], ...] = (
    (30, "checkpoint_refs"),
    (50, "summarize_old_evidence"),
    (70, "stop_new_discovery"),
    (80, "force_checkpoint"),
)


def estimate_tokens(text: str) -> int:
    """Estimate tokens conservatively as ceil(chars / 4)."""
    return math.ceil(len(text) / 4)


def _validate_tokens(name: str, value: int, *, allow_zero: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0 or (value == 0 and not allow_zero):
        if allow_zero:
            raise ValueError(f"{name} must be greater than or equal to 0")
        raise ValueError(f"{name} must be greater than 0")
    return value


def context_usage(used_tokens: int, max_tokens: int) -> dict[str, Any]:
    """Return context usage ratio, percent, stage, and required actions."""
    used_tokens = _validate_tokens("used_tokens", used_tokens, allow_zero=True)
    max_tokens = _validate_tokens("max_tokens", max_tokens, allow_zero=False)

    ratio = used_tokens / max_tokens
    percent = ratio * 100
    actions = [action for threshold, action in THRESHOLD_ACTIONS if percent >= threshold]
    stage = actions[-1] if actions else "normal"
    return {
        "used_tokens": used_tokens,
        "max_tokens": max_tokens,
        "ratio": ratio,
        "percent": percent,
        "stage": stage,
        "actions": actions,
    }


def build_context_budget_event(
    process_id: str,
    used_tokens: int | None = None,
    max_tokens: int | None = None,
    text: str | None = None,
    source: str = "estimated",
) -> dict[str, Any]:
    """Build a JSON-serializable context budget event without raw text."""
    if not process_id:
        raise ValueError("process_id is required")
    if max_tokens is None:
        raise ValueError("max_tokens is required")
    if used_tokens is None:
        if text is None:
            raise ValueError("used_tokens or text is required")
        used_tokens = estimate_tokens(text)

    usage = context_usage(used_tokens, max_tokens)
    event: dict[str, Any] = {
        "event": "context_budget",
        "process_id": process_id,
        "source": source,
        **usage,
    }
    if text is not None:
        event["text_chars"] = len(text)
    return event


def _read_cli_text(args: argparse.Namespace) -> str:
    if args.text is not None:
        return args.text
    if args.text_file is not None:
        return Path(args.text_file).read_text(encoding="utf-8")
    raise ValueError("--text or --text-file is required")


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate Hermes context budget usage")
    subparsers = parser.add_subparsers(dest="command", required=True)

    estimate_parser = subparsers.add_parser("estimate", help="Estimate context usage from text")
    estimate_parser.add_argument("--max-tokens", type=int, required=True)
    estimate_parser.add_argument("--text")
    estimate_parser.add_argument("--text-file")
    estimate_parser.add_argument("--process-id", default="cli")
    estimate_parser.add_argument("--source", default="estimated")

    args = parser.parse_args()
    if args.command == "estimate":
        try:
            if args.text is not None and args.text_file is not None:
                raise ValueError("use only one of --text or --text-file")
            text = _read_cli_text(args)
            event = build_context_budget_event(
                process_id=args.process_id,
                max_tokens=args.max_tokens,
                text=text,
                source=args.source,
            )
        except (OSError, TypeError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(event, ensure_ascii=False, sort_keys=True))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
