"""Session Tags — semantic tagging and search for Hermes process sessions.

Tags are derived automatically from Router classification (task_level, task_type,
risk_level, skill tags, process_plan stages) and saved to the supervisor store
after each task reaches a final status.

Tag Hierarchy (business tree):
  core/architecture/ADR
  core/architecture/C4
  core/code/code_change
  core/code/code_review
  core/code/TDD
  core/code/refactoring
  core/code/bugfix
  core/code/testing
  core/pipeline/supervisor
  core/pipeline/bot1_execution
  core/pipeline/bot2_gate
  core/pipeline/human_escalation
  devops/CI_CD
  devops/docker
  devops/deploy
  devops/monitoring
  domain/finance
  domain/analytics
  domain/excel
  domain/tender
  domain/client
  process/planning
  process/spike
  process/documentation
  process/report
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Schema
SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_tags (
    session_id TEXT NOT NULL,
    tag_path TEXT NOT NULL,         -- 'core/code/code_change'
    tag_label TEXT NOT NULL,        -- human-readable: 'Код: изменение кода'
    domain TEXT NOT NULL DEFAULT '', -- top-level category: 'core', 'devops', 'domain', 'process'
    level TEXT NOT NULL DEFAULT '', -- L0-L4
    risk TEXT NOT NULL DEFAULT '',  -- low/medium/high
    task_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    task_title TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (session_id, tag_path)
);
CREATE INDEX IF NOT EXISTS idx_session_tags_domain ON session_tags(domain);
CREATE INDEX IF NOT EXISTS idx_session_tags_level ON session_tags(level);
CREATE INDEX IF NOT EXISTS idx_session_tags_risk ON session_tags(risk);
CREATE INDEX IF NOT EXISTS idx_session_tags_tag_path ON session_tags(tag_path);
CREATE INDEX IF NOT EXISTS idx_session_tags_created ON session_tags(created_at);
"""

# Tag hierarchy: path -> human label
TAG_HIERARCHY: Dict[str, str] = {
    "core": "Ядро",
    "core/architecture": "Архитектура",
    "core/architecture/ADR": "ADR (Architecture Decision Record)",
    "core/architecture/C4": "C4-диаграммы",
    "core/architecture/EventStorming": "Event Storming",
    "core/code": "Код",
    "core/code/code_change": "Изменение кода",
    "core/code/code_review": "Code Review",
    "core/code/TDD": "TDD",
    "core/code/refactoring": "Рефакторинг",
    "core/code/bugfix": "Исправление бага",
    "core/code/testing": "Тестирование",
    "core/pipeline": "Pipeline",
    "core/pipeline/supervisor": "Supervisor (процесс)",
    "core/pipeline/bot1_execution": "Bot#1 исполнение",
    "core/pipeline/bot2": "Bot#2 gate",
    "core/pipeline/bot2_gate": "Bot#2 gate (review)",
    "core/pipeline/human_escalation": "Эскалация человеку",
    "devops": "DevOps",
    "devops/CI_CD": "CI/CD",
    "devops/docker": "Docker",
    "devops/deploy": "Деплой",
    "devops/monitoring": "Мониторинг",
    "domain": "Домен бизнеса",
    "domain/finance": "Финансы",
    "domain/analytics": "Аналитика",
    "domain/excel": "Excel/данные",
    "domain/tender": "Тендер/закупки",
    "domain/client": "Клиент",
    "process": "Процесс",
    "process/planning": "Планирование",
    "process/spike": "Spike/исследование",
    "process/documentation": "Документация",
    "process/report": "Отчёт",
}

# Task type -> tag path mapping
TASK_TYPE_TAGS: Dict[str, List[str]] = {
    "code_change": ["core/code/code_change"],
    "code_or_deploy_project": ["core/code/code_change", "devops/deploy"],
    "bugfix": ["core/code/bugfix"],
    "code_review": ["core/code/code_review"],
    "testing": ["core/code/testing"],
    "refactoring": ["core/code/refactoring"],
    "architecture": ["core/architecture"],
    "architecture_or_strategy": ["core/architecture", "process/planning"],
    "adr": ["core/architecture/ADR"],
    "c4_model": ["core/architecture/C4"],
    "event_storming": ["core/architecture/EventStorming"],
    "database_migration_plan": ["core/architecture", "process/planning", "core/code/testing"],
    "database_migration_change": ["core/code/code_change", "devops/deploy", "core/code/testing"],
    "git_write_or_deploy": ["devops/deploy", "core/pipeline/human_escalation"],
    "supplier_price_deadline_analysis": ["domain/tender", "domain/analytics"],
    "planning": ["process/planning"],
    "spike": ["process/spike"],
    "documentation": ["process/documentation"],
    "report": ["process/report"],
    "analysis": ["domain/analytics"],
    "excel": ["domain/excel"],
    "finance": ["domain/finance"],
    "tender": ["domain/tender"],
    "client": ["domain/client"],
    "ci_cd": ["devops/CI_CD"],
    "deploy": ["devops/deploy"],
    "docker": ["devops/docker"],
    "monitoring": ["devops/monitoring"],
    "standard_task": ["process"],
}

# Skill tags in manifest.json -> tag paths
SKILL_TAG_MAP: Dict[str, str] = {
    "adr": "core/architecture/ADR",
    "c4": "core/architecture/C4",
    "c4-model": "core/architecture/C4",
    "event-storming": "core/architecture/EventStorming",
    "code": "core/code/code_change",
    "tdd": "core/code/TDD",
    "testing": "core/code/testing",
    "qa": "core/code/testing",
    "refactoring": "core/code/refactoring",
    "devops": "devops",
    "docker": "devops/docker",
    "deploy": "devops/deploy",
    "ci/cd": "devops/CI_CD",
    "analysis": "domain/analytics",
    "analytics": "domain/analytics",
    "tender": "domain/tender",
    "client": "domain/client",
    "finance": "domain/finance",
    "report": "process/report",
    "documentation": "process/documentation",
    "spike": "process/spike",
}


def get_store_path() -> Path:
    """Get the supervisor store path, respecting env override."""
    default = "/var/lib/docker/volumes/hermes-data/_data/supervisor_store.db"
    return Path(os.environ.get("SUPERVISOR_STORE", default))


def connect(store_path: Path | str | None = None) -> sqlite3.Connection:
    """Connect to the supervisor store and ensure session_tags table exists."""
    path = Path(store_path) if store_path else get_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA_SQL)
    return con


def build_tags_from_route(route: Dict[str, Any], task_title: str = "") -> List[Dict[str, str]]:
    """Build a list of tag dicts from Router classification output.

    Args:
        route: Router output dict (from task_router.classify_task)
        task_title: Human-readable task description

    Returns:
        List of dicts with keys: tag_path, tag_label, domain, level, risk, task_type
    """
    tags: List[Dict[str, str]] = []
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    level = route.get("task_level", "")
    risk = route.get("risk_level", "")
    task_type = route.get("task_type", "")

    # Level tag
    if level:
        tags.append(dict(
            tag_path=f"уровень/{level}",
            tag_label=f"Уровень сложности: {level}",
            domain="meta",
            level=level,
            risk=risk,
            task_type=task_type,
            created_at=now,
            task_title=task_title,
        ))

    # Risk tag
    if risk:
        tags.append(dict(
            tag_path=f"риск/{risk}",
            tag_label=f"Риск: {risk}",
            domain="meta",
            level=level,
            risk=risk,
            task_type=task_type,
            created_at=now,
            task_title=task_title,
        ))

    # Task type -> domain tags
    type_tags = TASK_TYPE_TAGS.get(task_type, [])
    for tp in type_tags:
        domain = tp.split("/")[0]
        label = TAG_HIERARCHY.get(tp, tp)
        tags.append(dict(
            tag_path=tp,
            tag_label=label,
            domain=domain,
            level=level,
            risk=risk,
            task_type=task_type,
            created_at=now,
            task_title=task_title,
        ))

    # Skill tags from router context
    skill_context = route.get("skill_context", {})
    selected_skills = skill_context.get("selected_skills", [])
    for skill in selected_skills:
        skill_tags = skill.get("tags", [])
        for st in skill_tags:
            mapped = SKILL_TAG_MAP.get(st)
            if mapped and mapped not in [t["tag_path"] for t in tags]:
                domain = mapped.split("/")[0]
                label = TAG_HIERARCHY.get(mapped, mapped)
                tags.append(dict(
                    tag_path=mapped,
                    tag_label=label,
                    domain=domain,
                    level=level,
                    risk=risk,
                    task_type=task_type,
                    created_at=now,
                    task_title=task_title,
                ))

    # Process plan tags — map stage names to tag paths
    process_plan = route.get("process_plan", [])
    STAGE_TAG_MAP = {
        "bot1": "core/pipeline/bot1_execution",
        "bot2": "core/pipeline/bot2",
        "bot2_light_if_risky": "core/pipeline/bot2",
        "supervisor": "core/pipeline/supervisor",
        "tester": "core/code/testing",
        "architect": "core/architecture",
        "devops_if_approved": "devops/deploy",
        "human_decision": "core/pipeline/human_escalation",
    }
    for stage in process_plan:
        stage_tag = STAGE_TAG_MAP.get(stage)
        if stage_tag and stage_tag not in [t["tag_path"] for t in tags]:
            tags.append(dict(
                tag_path=stage_tag,
                tag_label=TAG_HIERARCHY[stage_tag],
                domain=stage_tag.split("/")[0],
                level=level,
                risk=risk,
                task_type=task_type,
                created_at=now,
                task_title=task_title,
            ))

    return tags


def save_session_tags(
    session_id: str,
    route: Dict[str, Any],
    task_title: str = "",
    store_path: Path | str | None = None,
) -> int:
    """Derive tags from Router route and save to supervisor store.

    Args:
        session_id: Process or bot2 session ID
        route: Router output dict
        task_title: Human-readable task title
        store_path: Override store path (for testing)

    Returns:
        Number of tags saved
    """
    tags = build_tags_from_route(route, task_title)
    if not tags:
        return 0

    con = connect(store_path)
    try:
        for tag in tags:
            con.execute(
                """INSERT OR REPLACE INTO session_tags
                   (session_id, tag_path, tag_label, domain, level, risk,
                    task_type, created_at, task_title)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    tag["tag_path"],
                    tag["tag_label"],
                    tag["domain"],
                    tag["level"],
                    tag["risk"],
                    tag["task_type"],
                    tag["created_at"],
                    tag["task_title"],
                ),
            )
        con.commit()
        return len(tags)
    finally:
        con.close()


def search_by_tags(
    domains: Optional[List[str]] = None,
    levels: Optional[List[str]] = None,
    risks: Optional[List[str]] = None,
    task_types: Optional[List[str]] = None,
    tag_paths: Optional[List[str]] = None,
    limit: int = 20,
    offset: int = 0,
    store_path: Path | str | None = None,
) -> List[Dict[str, Any]]:
    """Search sessions by tag criteria.

    All criteria are AND-ed. Returns distinct sessions.
    """
    con = connect(store_path)
    try:
        conditions: List[str] = []
        params: List[str] = []

        if domains:
            placeholders = ",".join("?" * len(domains))
            conditions.append(f"domain IN ({placeholders})")
            params.extend(domains)
        if levels:
            placeholders = ",".join("?" * len(levels))
            conditions.append(f"level IN ({placeholders})")
            params.extend(levels)
        if risks:
            placeholders = ",".join("?" * len(risks))
            conditions.append(f"risk IN ({placeholders})")
            params.extend(risks)
        if task_types:
            placeholders = ",".join("?" * len(task_types))
            conditions.append(f"task_type IN ({placeholders})")
            params.extend(task_types)
        if tag_paths:
            placeholders = ",".join("?" * len(tag_paths))
            conditions.append(f"tag_path IN ({placeholders})")
            params.extend(tag_paths)

        where = " AND ".join(conditions) if conditions else "1=1"

        # First get distinct sessions
        rows = con.execute(
            f"""SELECT DISTINCT session_id, level, risk, task_type,
                       MIN(created_at) as created_at,
                       MIN(task_title) as task_title
                FROM session_tags
                WHERE {where}
                GROUP BY session_id
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?""",
            params + [max(1, int(limit)), max(0, int(offset))],
        ).fetchall()

        results = []
        for row in rows:
            # Get all tags for this session
            tag_rows = con.execute(
                "SELECT tag_path, tag_label, domain FROM session_tags WHERE session_id = ? ORDER BY tag_path",
                (row["session_id"],),
            ).fetchall()
            results.append({
                "session_id": row["session_id"],
                "level": row["level"],
                "risk": row["risk"],
                "task_type": row["task_type"],
                "created_at": row["created_at"],
                "task_title": row["task_title"],
                "tags": [dict(t) for t in tag_rows],
            })
        return results
    finally:
        con.close()


def count_by_domain(
    store_path: Path | str | None = None,
) -> List[Dict[str, Any]]:
    """Count sessions grouped by domain."""
    con = connect(store_path)
    try:
        rows = con.execute(
            """SELECT domain, COUNT(DISTINCT session_id) as count
               FROM session_tags
               GROUP BY domain
               ORDER BY count DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get_tag_tree() -> Dict[str, Any]:
    """Return the full tag hierarchy tree."""
    tree: Dict[str, Any] = {}
    for path, label in sorted(TAG_HIERARCHY.items()):
        parts = path.split("/")
        current = tree
        for i, part in enumerate(parts):
            subpath = "/".join(parts[: i + 1])
            if part not in current:
                current[part] = {
                    "_label": label if subpath == path else TAG_HIERARCHY.get(subpath, part),
                    "_path": subpath,
                }
            current = current[part]
    return tree
