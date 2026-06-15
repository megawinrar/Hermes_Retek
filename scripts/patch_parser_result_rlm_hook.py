#!/usr/bin/env python3
"""Patch Hermes tool executor to record parser result artifacts in RLM."""

from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


PATCH_MARKER = "HERMES_RETEK_PARSER_RESULT_RLM_HOOK"

IMPORT_ANCHOR = "import random\n"
IMPORT_BLOCK = "import random\nimport re\n"

HELPER_ANCHOR = "logger = logging.getLogger(__name__)\n\n# Maximum number"
HELPER_BLOCK = f'''logger = logging.getLogger(__name__)


# {PATCH_MARKER}: persist useful browser/parser output files as compact RLM lessons.
def _hermes_retek_parser_result_paths(function_args, function_result) -> list[str]:
    try:
        raw = json.dumps(function_args, ensure_ascii=False, default=str) + "\\n" + str(function_result)
    except Exception:
        raw = str(function_args) + "\\n" + str(function_result)
    candidates = re.findall(r"/opt/data/rebrowser/[^\\s'\\\"<>]+\\.(?:json|csv)", raw, flags=re.IGNORECASE)
    seen: set[str] = set()
    paths: list[str] = []
    for candidate in candidates:
        cleaned = candidate.rstrip(".,);:]")
        lowered = cleaned.lower()
        if cleaned in seen:
            continue
        if not (lowered.endswith(".json") or lowered.endswith(".csv")):
            continue
        seen.add(cleaned)
        paths.append(cleaned)
    return paths


def _hermes_retek_infer_parser_site(path: str, function_args, function_result) -> str:
    raw = f"{{path}} {{function_args}} {{function_result}}".lower()
    if "b2b" in raw or "b2b-center" in raw:
        return "b2b_center"
    if "kontur" in raw or "zakupki" in raw or "контур" in raw or "парсинг-лома" in raw:
        return "kontur_zakupki"
    return "unknown_site"


def _hermes_retek_maybe_record_parser_result(function_name: str, function_args, function_result, process_id: str = "") -> None:
    if os.environ.get("HERMES_PARSER_RESULT_RLM_ENABLED", "1").strip().lower() in {{"0", "false", "no", "off"}}:
        return
    if function_name not in {{"execute_code", "terminal", "write_file", "patch", "browser_snapshot", "read_file"}}:
        return
    paths = _hermes_retek_parser_result_paths(function_args, function_result)
    if not paths:
        return
    try:
        import sys as _sys
        _scripts_dir = os.environ.get("HERMES_ASSISTANT_SCRIPTS", "/opt/hermes-assistant/scripts")
        if _scripts_dir and _scripts_dir not in _sys.path:
            _sys.path.insert(0, _scripts_dir)
        from parser_result_rlm import write_parser_result_lesson as _write_parser_result_lesson

        for path in paths[:5]:
            if not os.path.exists(path):
                continue
            _write_parser_result_lesson(
                path,
                store_path=os.environ.get("HERMES_RLM_STORE_PATH", "/opt/data/rlm_store.db"),
                process_id=process_id,
                site=_hermes_retek_infer_parser_site(path, function_args, function_result),
            )
    except Exception as exc:
        logger.debug("parser result RLM hook failed: %s", exc)

# Maximum number'''.rstrip()

CONCURRENT_ANCHOR = '''            is_error, _ = _detect_tool_failure(function_name, result)
            if is_error:
                logger.info("tool %s failed (%.2fs): %s", function_name, duration, result[:200])
            else:
                logger.info("tool %s completed (%.2fs, %d chars)", function_name, duration, len(result))
            results[index] = (function_name, function_args, result, duration, is_error, False, middleware_trace)
'''
CONCURRENT_BLOCK = '''            is_error, _ = _detect_tool_failure(function_name, result)
            if is_error:
                logger.info("tool %s failed (%.2fs): %s", function_name, duration, result[:200])
            else:
                logger.info("tool %s completed (%.2fs, %d chars)", function_name, duration, len(result))
                _hermes_retek_maybe_record_parser_result(
                    function_name,
                    function_args,
                    result,
                    getattr(agent, "_current_task_id", "") or getattr(agent, "session_id", "") or "",
                )
            results[index] = (function_name, function_args, result, duration, is_error, False, middleware_trace)
'''

SEQUENTIAL_ANCHOR = '''        _is_error_result, _ = _detect_tool_failure(function_name, function_result)
        # The agent-runtime tools above (todo, session_search, memory,
'''
SEQUENTIAL_BLOCK = '''        _is_error_result, _ = _detect_tool_failure(function_name, function_result)
        if not _is_error_result:
            _hermes_retek_maybe_record_parser_result(
                function_name,
                function_args,
                function_result,
                getattr(agent, "_current_task_id", "") or getattr(agent, "session_id", "") or "",
            )
        # The agent-runtime tools above (todo, session_search, memory,
'''


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-parser-result-rlm-{stamp}")


def patch_parser_result_rlm_hook(source: str) -> tuple[str, bool]:
    """Return patched source and whether it changed."""
    if PATCH_MARKER in source:
        return source, False
    updated = source
    if "import re\n" not in updated:
        if IMPORT_ANCHOR not in updated:
            raise ValueError("import anchor not found")
        updated = updated.replace(IMPORT_ANCHOR, IMPORT_BLOCK, 1)
    if HELPER_ANCHOR not in updated:
        raise ValueError("helper anchor not found")
    if CONCURRENT_ANCHOR not in updated:
        raise ValueError("concurrent anchor not found")
    if SEQUENTIAL_ANCHOR not in updated:
        raise ValueError("sequential anchor not found")
    updated = updated.replace(HELPER_ANCHOR, HELPER_BLOCK, 1)
    updated = updated.replace(CONCURRENT_ANCHOR, CONCURRENT_BLOCK, 1)
    updated = updated.replace(SEQUENTIAL_ANCHOR, SEQUENTIAL_BLOCK, 1)
    return updated, updated != source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Path to agent/tool_executor.py")
    parser.add_argument("--no-backup", action="store_true", help="Do not write a timestamped backup")
    args = parser.parse_args()

    source = args.path.read_text(encoding="utf-8")
    updated, changed = patch_parser_result_rlm_hook(source)
    if not changed:
        print("parser_result_rlm_hook=already_present")
        return 0
    if not args.no_backup:
        shutil.copy2(args.path, backup_path(args.path))
    args.path.write_text(updated, encoding="utf-8")
    print("parser_result_rlm_hook=applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
