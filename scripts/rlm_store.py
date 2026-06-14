#!/usr/bin/env python3
"""RLM-lite durable record store for Hermes.

This is a small SQLite-backed memory/artifact/event store. It intentionally
does not implement vector search or RAG; search is simple SQLite LIKE over
record title, summary, and tags.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from secret_patterns import redact_text
except ImportError:  # pragma: no cover - package-style import fallback
    from scripts.secret_patterns import redact_text


DEFAULT_STORE_PATH = Path("/var/lib/docker/volumes/hermes-data/_data/rlm_store.db")
SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rlm_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    tags_text TEXT NOT NULL DEFAULT '',
    process_id TEXT NOT NULL DEFAULT '',
    importance REAL NOT NULL DEFAULT 0.5,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_rlm_records_kind ON rlm_records(kind);
CREATE INDEX IF NOT EXISTS idx_rlm_records_process_id ON rlm_records(process_id);
CREATE INDEX IF NOT EXISTS idx_rlm_records_importance ON rlm_records(importance);
CREATE INDEX IF NOT EXISTS idx_rlm_records_created_at ON rlm_records(created_at);
CREATE INDEX IF NOT EXISTS idx_rlm_records_tags_text ON rlm_records(tags_text);

CREATE TABLE IF NOT EXISTS rlm_schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(raw: str | None, default: Any) -> Any:
    if raw is None or raw == "":
        return default
    return json.loads(raw)


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def get_store_path() -> Path:
    return Path(os.environ.get("HERMES_RLM_STORE_PATH", str(DEFAULT_STORE_PATH)))


def connect(store_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(store_path) if store_path else get_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    init_schema(con)
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL)
    con.execute(
        "INSERT OR REPLACE INTO rlm_schema_meta(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    con.commit()


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {str(_redact_value(key)): _redact_value(item) for key, item in value.items()}
    return value


def _normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    if not tags:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        safe = str(_redact_value(str(tag))).strip()
        if safe and safe not in seen:
            normalized.append(safe)
            seen.add(safe)
    return normalized


def _tags_text(tags: list[str]) -> str:
    if not tags:
        return ""
    return "\n" + "\n".join(tags) + "\n"


def _row_to_record(row: sqlite3.Row, *, include_content: bool) -> dict[str, Any]:
    record = {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "summary": row["summary"],
        "tags": loads(row["tags_json"], []),
        "process_id": row["process_id"],
        "importance": row["importance"],
        "metadata": loads(row["metadata_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    if include_content:
        record["content"] = row["content"]
    return record


def add_record(
    kind: str,
    title: str,
    summary: str,
    content: str = "",
    tags: list[str] | None = None,
    process_id: str = "",
    importance: float = 0.5,
    metadata: dict[str, Any] | None = None,
    *,
    store_path: Path | str | None = None,
    redact: bool = True,
) -> dict[str, Any]:
    safe = _redact_value if redact else (lambda value: value)
    now = utc_now()
    safe_tags = _normalize_tags(safe(tags or []))
    safe_metadata = safe(metadata or {})

    with connect(store_path) as con:
        cur = con.execute(
            """
            INSERT INTO rlm_records (
                kind, title, summary, content, tags_json, tags_text,
                process_id, importance, metadata_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(safe(kind)).strip(),
                str(safe(title)).strip(),
                str(safe(summary)).strip(),
                str(safe(content)),
                dumps(safe_tags),
                _tags_text(safe_tags),
                str(safe(process_id)).strip(),
                float(importance),
                dumps(safe_metadata),
                now,
                now,
            ),
        )
        record_id = int(cur.lastrowid)
        row = con.execute("SELECT * FROM rlm_records WHERE id = ?", (record_id,)).fetchone()
    return _row_to_record(row, include_content=True)


def search_records(
    query: str = "",
    tags: list[str] | None = None,
    process_id: str = "",
    kind: str = "",
    limit: int = 20,
    *,
    store_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    query = query.strip()
    if query:
        needle = f"%{query}%"
        clauses.append("(title LIKE ? OR summary LIKE ? OR tags_text LIKE ?)")
        params.extend([needle, needle, needle])

    for tag in _normalize_tags(tags or []):
        clauses.append("tags_text LIKE ?")
        params.append(f"%\n{tag}\n%")

    if process_id:
        clauses.append("process_id = ?")
        params.append(process_id)

    if kind:
        clauses.append("kind = ?")
        params.append(kind)

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    safe_limit = max(1, min(int(limit), 200))

    with connect(store_path) as con:
        rows = con.execute(
            f"""
            SELECT * FROM rlm_records
            {where}
            ORDER BY importance DESC, created_at DESC, id DESC
            LIMIT ?
            """,
            (*params, safe_limit),
        ).fetchall()
    return [_row_to_record(row, include_content=False) for row in rows]


def get_record(
    record_id: int,
    *,
    store_path: Path | str | None = None,
) -> dict[str, Any] | None:
    with connect(store_path) as con:
        row = con.execute("SELECT * FROM rlm_records WHERE id = ?", (int(record_id),)).fetchone()
    if row is None:
        return None
    return _row_to_record(row, include_content=True)


def _artifact_ref(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") or {}
    for key in ("path", "url", "ref", "artifact_ref"):
        value = metadata.get(key)
        if value:
            return f" artifact={value}"
    if record.get("kind") == "artifact":
        return f" artifact=rlm:{record['id']}"
    return ""


def _context_line(record: dict[str, Any]) -> str:
    tags = ",".join(record.get("tags") or [])
    tags_part = f" tags={tags}" if tags else ""
    process_part = f" process={record['process_id']}" if record.get("process_id") else ""
    return (
        f"[{record['id']}] {record['kind']} {record['title']}: "
        f"{record['summary']}{tags_part}{process_part}{_artifact_ref(record)}"
    )


def _truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _context_line_with_budget(record: dict[str, Any], max_chars: int) -> str:
    line = _context_line(record)
    if len(line) <= max_chars:
        return line

    artifact = _artifact_ref(record)
    prefix = f"[{record['id']}] {record['kind']} {record['title']}: "
    suffix = artifact
    process_id = record.get("process_id")
    if process_id:
        suffix = f" process={process_id}{suffix}"

    fixed_chars = len(prefix) + len(suffix)
    summary_budget = max_chars - fixed_chars
    if summary_budget >= 12:
        return f"{prefix}{_truncate(str(record['summary']), summary_budget)}{suffix}"

    compact = f"[{record['id']}] {record['kind']} {record['title']}{artifact}"
    return _truncate(compact, max_chars)


def build_context_pack(
    query: str = "",
    tags: list[str] | None = None,
    process_id: str = "",
    token_budget: int = 800,
    *,
    kind: str = "",
    store_path: Path | str | None = None,
) -> dict[str, Any]:
    budget = max(0, int(token_budget))
    records = search_records(
        query=query,
        tags=tags,
        process_id=process_id,
        kind=kind,
        limit=100,
        store_path=store_path,
    )

    selected: list[dict[str, Any]] = []
    lines: list[str] = []
    max_chars = budget * 4

    for record in records:
        current_chars = len("\n".join(lines))
        remaining_chars = max_chars - current_chars - (1 if lines else 0)
        if remaining_chars <= 0:
            break
        line = _context_line_with_budget(record, remaining_chars)
        candidate = "\n".join([*lines, line]) if lines else line
        if estimate_tokens(candidate) > budget:
            continue
        selected.append(record)
        lines.append(line)

    context = "\n".join(lines)
    return {
        "token_budget": budget,
        "estimated_tokens": estimate_tokens(context),
        "context": context[:max_chars],
        "records": selected,
    }


def _parse_tags(raw: str | None, repeated: list[str] | None) -> list[str]:
    values: list[str] = []
    if raw:
        values.extend(part.strip() for part in raw.split(","))
    for tag in repeated or []:
        values.extend(part.strip() for part in tag.split(","))
    return [value for value in values if value]


def _parse_metadata(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise argparse.ArgumentTypeError("--metadata must be a JSON object")
    return loaded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes RLM-lite SQLite store")
    parser.add_argument("--store", default="", help="SQLite store path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="Add a record")
    add.add_argument("--kind", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--summary", required=True)
    add.add_argument("--content", default="")
    add.add_argument("--tags", default="")
    add.add_argument("--tag", action="append", default=[])
    add.add_argument("--process-id", default="")
    add.add_argument("--importance", type=float, default=0.5)
    add.add_argument("--metadata", type=_parse_metadata, default={})
    add.add_argument("--no-redact", action="store_true")

    search = subparsers.add_parser("search", help="Search records")
    search.add_argument("--query", default="")
    search.add_argument("--tags", default="")
    search.add_argument("--tag", action="append", default=[])
    search.add_argument("--process-id", default="")
    search.add_argument("--kind", default="")
    search.add_argument("--limit", type=int, default=20)

    pack = subparsers.add_parser("pack", help="Build a compact context pack")
    pack.add_argument("--query", default="")
    pack.add_argument("--tags", default="")
    pack.add_argument("--tag", action="append", default=[])
    pack.add_argument("--process-id", default="")
    pack.add_argument("--kind", default="")
    pack.add_argument("--token-budget", type=int, default=800)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store_path = args.store or None

    if args.command == "add":
        result = add_record(
            kind=args.kind,
            title=args.title,
            summary=args.summary,
            content=args.content,
            tags=_parse_tags(args.tags, args.tag),
            process_id=args.process_id,
            importance=args.importance,
            metadata=args.metadata,
            store_path=store_path,
            redact=not args.no_redact,
        )
    elif args.command == "search":
        result = search_records(
            query=args.query,
            tags=_parse_tags(args.tags, args.tag),
            process_id=args.process_id,
            kind=args.kind,
            limit=args.limit,
            store_path=store_path,
        )
    elif args.command == "pack":
        result = build_context_pack(
            query=args.query,
            tags=_parse_tags(args.tags, args.tag),
            process_id=args.process_id,
            kind=args.kind,
            token_budget=args.token_budget,
            store_path=store_path,
        )
    else:  # pragma: no cover - argparse enforces commands
        parser.error("unknown command")

    print(dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
