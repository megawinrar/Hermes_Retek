from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import patch_delegate_subcall_rlm  # noqa: E402


DELEGATE_SNIPPET = '''import logging
logger = logging.getLogger(__name__)
import os

def _run_single_child(task_index, goal, child=None, parent_agent=None, **_kwargs):
    child_start = time.monotonic()
    _raw_sid = getattr(child, "_subagent_id", None)
    _subagent_id = _raw_sid if isinstance(_raw_sid, str) else None
    if _subagent_id:
        _raw_depth = getattr(child, "_delegate_depth", 1)
        _tui_depth = max(0, _raw_depth - 1) if isinstance(_raw_depth, int) else 0
        _parent_sid = getattr(child, "_parent_subagent_id", None)
    try:
        import uuid as _uuid
        child_task_id = _subagent_id or f"subagent-{task_index}-{_uuid.uuid4().hex[:8]}"
        child_timeout = _get_child_timeout()
        _timeout_executor = ThreadPoolExecutor(
            max_workers=1,
        )
        try:
            result = _child_future.result(timeout=child_timeout)
        except Exception as _timeout_exc:
            is_timeout = isinstance(_timeout_exc, (FuturesTimeoutError, TimeoutError))
            duration = round(time.monotonic() - child_start, 2)
            child_api_calls = 0
            diagnostic_path = None
            if is_timeout:
                if child_api_calls == 0:
                    _err = "timeout"
            return {
                "task_index": task_index,
            }
        status = "completed"
        summary = "done"
        api_calls = 1
        duration = 1.2
        exit_reason = "completed"
        entry = {"status": status}
        if child_progress_cb:
            try:
                child_progress_cb("subagent.complete", **complete_kwargs)
            except Exception as e:
                logger.debug("Progress callback completion failed: %s", e)

        return entry

    except Exception as exc:
        duration = round(time.monotonic() - child_start, 2)
        if child_progress_cb:
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
            "task_index": task_index,
        }
'''


def test_patch_delegate_subcall_rlm_inserts_lifecycle_hooks() -> None:
    updated, changed = patch_delegate_subcall_rlm.patch_delegate_subcall_rlm(DELEGATE_SNIPPET)

    assert changed is True
    assert patch_delegate_subcall_rlm.PATCH_MARKER in updated
    assert "def _record_subcall_event(" in updated
    assert "_parent_sid = None" in updated
    assert 'status="started"' in updated
    assert 'status="timeout" if is_timeout else "error"' in updated
    assert "summary=summary or entry.get" in updated
    assert 'metadata={"exit_reason": "exception"}' in updated


def test_patch_delegate_subcall_rlm_is_idempotent() -> None:
    updated, changed = patch_delegate_subcall_rlm.patch_delegate_subcall_rlm(DELEGATE_SNIPPET)
    second, changed_again = patch_delegate_subcall_rlm.patch_delegate_subcall_rlm(updated)

    assert changed is True
    assert changed_again is False
    assert second == updated
    assert second.count(patch_delegate_subcall_rlm.PATCH_MARKER) == 1
