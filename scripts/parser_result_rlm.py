#!/usr/bin/env python3
"""Write compact RLM lessons for parser result artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

import rlm_store

try:
    from secret_patterns import redact_payload
except ImportError:  # pragma: no cover
    from scripts.secret_patterns import redact_payload


PARSER_RESULT_SUFFIXES = {".json", ".csv"}
RE_BROWSER_RESULT_RE = re.compile(
    r"/opt/data/rebrowser/[^\s'\"<>]+\.(?:json|csv)",
    flags=re.IGNORECASE,
)
RE_BROWSER_SCRIPT_RE = re.compile(
    r"(?P<dir>/opt/data/rebrowser/)(?P<prefix>[^/\s'\"<>]+?)-search(?:-v(?P<version>\d+))?\.js",
    flags=re.IGNORECASE,
)


def infer_parser_result_paths(function_args: Any, function_result: Any) -> list[str]:
    """Infer compact parser output artifacts from tool args/result text.

    Hermes-generated parser scripts often finish with a short human summary
    that omits the output filename. For the common `/opt/data/rebrowser/*`
    convention, infer result JSON names from script names so RLM recording is
    not dependent on the model remembering to print the path.
    """
    try:
        raw = json.dumps(function_args, ensure_ascii=False, default=str) + "\n" + str(function_result)
    except Exception:
        raw = str(function_args) + "\n" + str(function_result)

    paths: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        cleaned = path.rstrip(".,);:]")
        lowered = cleaned.lower()
        if cleaned in seen:
            return
        if not (lowered.endswith(".json") or lowered.endswith(".csv")):
            return
        seen.add(cleaned)
        paths.append(cleaned)

    for match in RE_BROWSER_RESULT_RE.finditer(raw):
        add(match.group(0))

    for match in RE_BROWSER_SCRIPT_RE.finditer(raw):
        directory = match.group("dir")
        prefix = match.group("prefix")
        version = match.group("version")
        suffix = f"-v{version}" if version else ""
        add(f"{directory}{prefix}-results{suffix}.json")

    return paths


def _count_json_items(data: Any) -> int:
    if isinstance(data, list):
        if all(isinstance(item, dict) and isinstance(item.get("sales"), list) for item in data):
            return sum(len(item.get("sales") or []) for item in data)
        return len(data)
    if isinstance(data, dict):
        for key in ("sales", "items", "results", "records", "rows"):
            value = data.get(key)
            if isinstance(value, list):
                return len(value)
        return sum(_count_json_items(value) for value in data.values() if isinstance(value, (list, dict)))
    return 0


def _json_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    queries = []
    if isinstance(data, list):
        for item in data[:12]:
            if isinstance(item, dict) and item.get("query"):
                queries.append(str(item.get("query")))
    return {
        "format": "json",
        "records": _count_json_items(data),
        "top_level_type": type(data).__name__,
        "queries": queries,
    }


def _csv_summary(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        rows = list(reader)
    header = rows[0] if rows else []
    return {
        "format": "csv",
        "records": max(0, len(rows) - 1 if header else len(rows)),
        "columns": header[:20],
    }


def summarize_parser_result(path: str | Path) -> dict[str, Any]:
    artifact = Path(path)
    if not artifact.exists():
        raise FileNotFoundError(str(artifact))
    if artifact.suffix.lower() not in PARSER_RESULT_SUFFIXES:
        raise ValueError(f"unsupported parser result suffix: {artifact.suffix}")
    summary = _json_summary(artifact) if artifact.suffix.lower() == ".json" else _csv_summary(artifact)
    summary.update(
        {
            "path": str(artifact),
            "filename": artifact.name,
            "size_bytes": artifact.stat().st_size,
        }
    )
    return redact_payload(summary)


def write_parser_result_lesson(
    path: str | Path,
    *,
    store_path: str | Path | None = None,
    process_id: str = "",
    site: str = "",
    script_path: str = "",
    title: str = "",
) -> dict[str, Any]:
    summary = summarize_parser_result(path)
    records = int(summary.get("records") or 0)
    site_tag = site or "unknown_site"
    metadata = {
        "artifact_path": str(path),
        "script_path": script_path,
        "site": site,
        "records": records,
        "size_bytes": int(summary.get("size_bytes") or 0),
    }
    try:
        with rlm_store.connect(store_path) as con:
            row = con.execute(
                """
                SELECT * FROM rlm_records
                WHERE kind = 'parser_result'
                  AND json_extract(metadata_json, '$.artifact_path') = ?
                  AND json_extract(metadata_json, '$.size_bytes') = ?
                ORDER BY id DESC
                LIMIT 1
            """,
            (str(path), metadata["size_bytes"]),
        ).fetchone()
        if row:
                existing = rlm_store.get_record(int(row["id"]), store_path=store_path) or {}
                existing["duplicate"] = True
                return existing
    except (sqlite3.Error, OSError, ValueError):
        pass

    title_value = title or f"Parser result {Path(path).name}"
    compact_summary = f"{site_tag}: {records} records in {summary.get('filename')} ({summary.get('size_bytes')} bytes)"
    content = {
        "artifact": summary,
        "site": site,
        "script_path": script_path,
        "repeat_hint": "Reuse the script and parser selectors recorded with this result; do not infer from chat history.",
    }
    return rlm_store.add_record(
        kind="parser_result",
        title=title_value,
        summary=compact_summary,
        content=json.dumps(redact_payload(content), ensure_ascii=False, sort_keys=True),
        tags=[
            "parser_result",
            "browser",
            "supplier",
            f"site/{site_tag}",
            f"format/{summary.get('format')}",
            "artifact",
        ],
        process_id=process_id,
        importance=0.88 if records else 0.55,
        metadata=metadata,
        store_path=store_path,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--store", default="")
    parser.add_argument("--process-id", default="")
    parser.add_argument("--site", default="")
    parser.add_argument("--script-path", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args(argv)
    if args.summary_only:
        payload = summarize_parser_result(args.path)
    else:
        payload = write_parser_result_lesson(
            args.path,
            store_path=args.store or None,
            process_id=args.process_id,
            site=args.site,
            script_path=args.script_path,
            title=args.title,
        )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
