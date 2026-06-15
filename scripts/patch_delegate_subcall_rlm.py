#!/usr/bin/env python3
"""Patch Hermes delegate_tool runtime to persist child-agent subcall records."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


PATCH_MARKER = "HERMES_RETEK_SUBCALL_RLM_PATCH"

HELPER_ANCHOR = "logger = logging.getLogger(__name__)\nimport os\n"
HELPER_BLOCK = f'''logger = logging.getLogger(__name__)

# {PATCH_MARKER}: persist child-agent lifecycle records without blocking runtime.
def _record_subcall_event(
    *,
    parent_agent,
    child,
    child_agent_id: str,
    parent_agent_id: str = "",
    depth: int = 0,
    status: str,
    goal: str = "",
    summary: str = "",
    timeout_seconds=None,
    token_budget=None,
    api_calls=None,
    duration_seconds=None,
    metadata=None,
) -> None:
    if os.environ.get("HERMES_RLM_SUBCALL_ENABLED", "1").strip().lower() in {{"0", "false", "no", "off"}}:
        return
    try:
        import sys as _sys
        _scripts_dir = os.environ.get("HERMES_ASSISTANT_SCRIPTS", "/opt/hermes-assistant/scripts")
        if _scripts_dir and _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        import rlm_store as _rlm_store

        parent_process_id = str(
            getattr(parent_agent, "_current_task_id", "")
            or getattr(parent_agent, "task_id", "")
            or ""
        )
        child_iterations = getattr(child, "max_iterations", None)
        merged_metadata = dict(metadata or {{}})
        if child_iterations is not None:
            merged_metadata.setdefault("max_iterations", child_iterations)
        _rlm_store.add_subcall_record(
            parent_process_id=parent_process_id,
            child_agent_id=child_agent_id,
            parent_agent_id=parent_agent_id,
            depth=depth,
            status=status,
            goal=goal,
            summary=summary,
            timeout_seconds=timeout_seconds,
            token_budget=token_budget,
            api_calls=api_calls,
            duration_seconds=duration_seconds,
            metadata=merged_metadata,
        )
    except Exception as exc:
        logger.debug("RLM subcall write failed: %s", exc)
import os
'''.rstrip()

START_ANCHOR = """        child_timeout = _get_child_timeout()
        _timeout_executor = ThreadPoolExecutor(
"""
START_BLOCK = """        child_timeout = _get_child_timeout()
        _record_subcall_event(
            parent_agent=parent_agent,
            child=child,
            child_agent_id=child_task_id,
            parent_agent_id=_parent_sid if isinstance(_parent_sid, str) else "",
            depth=_tui_depth if isinstance(_tui_depth, int) else 0,
            status="started",
            goal=goal,
            timeout_seconds=child_timeout,
            token_budget=getattr(child, "max_tokens", None),
        )
        _timeout_executor = ThreadPoolExecutor(
"""

TIMEOUT_ANCHOR = """            if is_timeout:
                if child_api_calls == 0:
"""
TIMEOUT_BLOCK = """            _record_subcall_event(
                parent_agent=parent_agent,
                child=child,
                child_agent_id=child_task_id,
                parent_agent_id=_parent_sid if isinstance(_parent_sid, str) else "",
                depth=_tui_depth if isinstance(_tui_depth, int) else 0,
                status="timeout" if is_timeout else "error",
                goal=goal,
                summary=_err if "_err" in locals() else str(_timeout_exc),
                timeout_seconds=child_timeout,
                token_budget=getattr(child, "max_tokens", None),
                api_calls=child_api_calls,
                duration_seconds=duration,
                metadata={"exit_reason": "timeout" if is_timeout else "error", "diagnostic_path": diagnostic_path},
            )

            if is_timeout:
                if child_api_calls == 0:
"""

SUCCESS_ANCHOR = """        if child_progress_cb:
            try:
                child_progress_cb("subagent.complete", **complete_kwargs)
            except Exception as e:
                logger.debug("Progress callback completion failed: %s", e)

        return entry
"""
SUCCESS_BLOCK = """        if child_progress_cb:
            try:
                child_progress_cb("subagent.complete", **complete_kwargs)
            except Exception as e:
                logger.debug("Progress callback completion failed: %s", e)

        _record_subcall_event(
            parent_agent=parent_agent,
            child=child,
            child_agent_id=child_task_id,
            parent_agent_id=_parent_sid if isinstance(_parent_sid, str) else "",
            depth=_tui_depth if isinstance(_tui_depth, int) else 0,
            status=status,
            goal=goal,
            summary=summary or entry.get("error", ""),
            timeout_seconds=child_timeout,
            token_budget=getattr(child, "max_tokens", None),
            api_calls=api_calls,
            duration_seconds=duration,
            metadata={"exit_reason": exit_reason},
        )

        return entry
"""

ERROR_ANCHOR = """        if child_progress_cb:
            try:
                child_progress_cb(
                    "subagent.complete",
                    preview=str(exc),
                    status="failed",
                    duration_seconds=duration,
                    summary=str(exc),
                )
            except Exception as e:
                logger.debug("Progress callback failure relay failed: %s", e)
        return {
"""
ERROR_BLOCK = """        if child_progress_cb:
            try:
                child_progress_cb(
                    "subagent.complete",
                    preview=str(exc),
                    status="failed",
                    duration_seconds=duration,
                    summary=str(exc),
                )
            except Exception as e:
                logger.debug("Progress callback failure relay failed: %s", e)
        _record_subcall_event(
            parent_agent=parent_agent,
            child=child,
            child_agent_id=_subagent_id or f"subagent-{task_index}",
            parent_agent_id=_parent_sid if isinstance(_parent_sid, str) else "",
            depth=_tui_depth if isinstance(_tui_depth, int) else 0,
            status="error",
            goal=goal,
            summary=str(exc),
            api_calls=0,
            duration_seconds=duration,
            metadata={"exit_reason": "exception"},
        )
        return {
"""

IDENTITY_ANCHOR = """    _raw_sid = getattr(child, "_subagent_id", None)
    _subagent_id = _raw_sid if isinstance(_raw_sid, str) else None
    if _subagent_id:
"""
IDENTITY_BLOCK = """    _raw_sid = getattr(child, "_subagent_id", None)
    _subagent_id = _raw_sid if isinstance(_raw_sid, str) else None
    _parent_sid = None
    _tui_depth = 0
    if _subagent_id:
"""


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-subcall-rlm-{stamp}")


def _replace_once(source: str, old: str, new: str) -> str:
    if old not in source:
        raise ValueError(f"anchor not found: {old.splitlines()[0]}")
    return source.replace(old, new, 1)


def patch_delegate_subcall_rlm(source: str) -> tuple[str, bool]:
    """Return patched source and whether it changed."""
    if PATCH_MARKER in source:
        return source, False
    updated = _replace_once(source, HELPER_ANCHOR, HELPER_BLOCK + "\n")
    updated = _replace_once(updated, IDENTITY_ANCHOR, IDENTITY_BLOCK)
    updated = _replace_once(updated, START_ANCHOR, START_BLOCK)
    updated = _replace_once(updated, TIMEOUT_ANCHOR, TIMEOUT_BLOCK)
    updated = _replace_once(updated, SUCCESS_ANCHOR, SUCCESS_BLOCK)
    updated = _replace_once(updated, ERROR_ANCHOR, ERROR_BLOCK)
    return updated, updated != source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to tools/delegate_tool.py")
    parser.add_argument("--no-backup", action="store_true", help="Do not write a timestamped backup")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    updated, changed = patch_delegate_subcall_rlm(source)
    if not changed:
        print("subcall_rlm_patch=already_present")
        return 0
    if not args.no_backup:
        shutil.copy2(args.path, backup_path(args.path))
    args.path.write_text(updated, encoding="utf-8")
    print("subcall_rlm_patch=applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
