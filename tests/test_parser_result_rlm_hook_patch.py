from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import patch_parser_result_rlm_hook  # noqa: E402


BASE_SNIPPET = '''import json
import logging
import os
import random
import time

logger = logging.getLogger(__name__)

# Maximum number of concurrent worker threads for parallel tool execution.
def concurrent(agent, function_name, function_args, result, duration, index, results, middleware_trace):
            is_error, _ = _detect_tool_failure(function_name, result)
            if is_error:
                logger.info("tool %s failed (%.2fs): %s", function_name, duration, result[:200])
            else:
                logger.info("tool %s completed (%.2fs, %d chars)", function_name, duration, len(result))
            results[index] = (function_name, function_args, result, duration, is_error, False, middleware_trace)

def sequential(agent, function_name, function_args, function_result):
        _is_error_result, _ = _detect_tool_failure(function_name, function_result)
        # The agent-runtime tools above (todo, session_search, memory,
    return function_result
'''


def test_patch_parser_result_rlm_hook_inserts_hooks() -> None:
    updated, changed = patch_parser_result_rlm_hook.patch_parser_result_rlm_hook(BASE_SNIPPET)

    assert changed is True
    assert patch_parser_result_rlm_hook.PATCH_MARKER in updated
    assert "import re" in updated
    assert "def _hermes_retek_maybe_record_parser_result(" in updated
    assert "infer_parser_result_paths" in updated
    assert "from parser_result_rlm import write_parser_result_lesson" in updated
    assert updated.count("_hermes_retek_maybe_record_parser_result(") == 3
    assert "not _is_error_result" in updated
    assert "parser result RLM hook recording" in updated
    assert "parser result RLM hook failed" in updated
    assert "logger.warning" in updated


def test_patch_parser_result_rlm_hook_is_idempotent() -> None:
    updated, changed = patch_parser_result_rlm_hook.patch_parser_result_rlm_hook(BASE_SNIPPET)
    second, changed_again = patch_parser_result_rlm_hook.patch_parser_result_rlm_hook(updated)

    assert changed is True
    assert changed_again is False
    assert second == updated
    assert second.count(patch_parser_result_rlm_hook.PATCH_MARKER) == 1


def test_patch_parser_result_rlm_hook_upgrades_old_marker() -> None:
    old, changed = patch_parser_result_rlm_hook.patch_parser_result_rlm_hook(BASE_SNIPPET)
    old = old.replace(
        patch_parser_result_rlm_hook.PATCH_MARKER,
        patch_parser_result_rlm_hook.OLD_PATCH_MARKER,
    ).replace(
        "from parser_result_rlm import infer_parser_result_paths as _infer_parser_result_paths\n\n        return _infer_parser_result_paths(function_args, function_result)",
        "raw = str(function_args) + str(function_result)\n        return re.findall(r\"/opt/data/rebrowser/[^\\\\s]+\\\\.(?:json|csv)\", raw)",
    )

    upgraded, upgraded_changed = patch_parser_result_rlm_hook.patch_parser_result_rlm_hook(old)

    assert changed is True
    assert upgraded_changed is True
    assert patch_parser_result_rlm_hook.PATCH_MARKER in upgraded
    assert "infer_parser_result_paths" in upgraded
    assert "return re.findall" not in upgraded
