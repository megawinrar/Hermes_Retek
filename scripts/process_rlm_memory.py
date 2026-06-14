#!/usr/bin/env python3
"""Process-to-RLM sidecar writer for Hermes.

The process/supervisor SQLite stores remain the source of truth for runtime
state. This module writes compact, redacted learning records into the RLM store
so future runs can retrieve process lessons without replaying full event logs.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from typing import Any

import rlm_store

try:
    from secret_patterns import redact_payload
except ImportError:  # pragma: no cover - package-style import fallback
    from scripts.secret_patterns import redact_payload


RLM_CONTENT_MAX_CHARS = 12000


@dataclass(frozen=True)
class RlmConfig:
    enabled: bool = False
    store_path: str | None = None


@dataclass(frozen=True)
class ProcessRlmSnapshot:
    process_id: str
    supervisor_task_id: str
    task: str
    acceptance: str
    route: dict[str, Any]
    skill_context: dict[str, Any]
    final_status: str
    bot1_result: str
    bot2_session_id: str
    verdict: dict[str, Any] = field(default_factory=dict)
    report_path: str = ""
    human_message: str = ""


@dataclass(frozen=True)
class RlmWriteOutcome:
    status: str
    records: list[dict[str, Any]] = field(default_factory=list)
    event_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def enabled_from_env() -> bool:
    return os.environ.get("HERMES_RLM_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def store_for_args(args: argparse.Namespace) -> str | None:
    value = str(getattr(args, "rlm_store", "") or "").strip()
    return value or None


def config_from_args(args: argparse.Namespace) -> RlmConfig:
    store_path = store_for_args(args)
    return RlmConfig(
        enabled=bool(getattr(args, "rlm_enabled", False) or store_path or enabled_from_env()),
        store_path=store_path,
    )


def write_enabled(args: argparse.Namespace) -> bool:
    return config_from_args(args).enabled


def truncate_content(value: str, *, limit: int = RLM_CONTENT_MAX_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 32].rstrip() + "\n...[truncated for RLM]"


def skill_names(skill_context: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for item in skill_context.get("selected_skills") or []:
        name = str((item or {}).get("name") or "")
        if name and name not in names:
            names.append(name)
    return names


def role_skill_context(skill_context: dict[str, Any], role: str) -> dict[str, Any]:
    return {
        "role": role,
        "skills": (skill_context.get("roles") or {}).get(role, []),
        "gated_skills": (skill_context.get("gated_roles") or {}).get(role, []),
        "task_tags": skill_context.get("task_tags", []),
        "tool_results": skill_context.get("tool_results", []),
        "runtime_contract": skill_context.get("runtime_contract", {}),
    }


def rlm_tags(route: dict[str, Any], skill_context: dict[str, Any], final_status: str) -> list[str]:
    tags = [
        "process",
        f"level/{route.get('task_level', '')}",
        f"type/{route.get('task_type', '')}",
        f"status/{final_status}",
    ]
    tags.extend(f"task/{tag}" for tag in skill_context.get("task_tags") or [])
    tags.extend(f"skill/{name}" for name in skill_names(skill_context))
    return [tag for tag in tags if not tag.endswith("/")]


def write_process_records(snapshot: ProcessRlmSnapshot, config: RlmConfig) -> list[dict[str, Any]]:
    if not config.enabled:
        return []

    selected_skill_names = skill_names(snapshot.skill_context)
    base_tags = rlm_tags(snapshot.route, snapshot.skill_context, snapshot.final_status)
    metadata = {
        "source": "process_orchestrator",
        "supervisor_task_id": snapshot.supervisor_task_id,
        "bot2_session_id": snapshot.bot2_session_id,
        "report_path": snapshot.report_path,
    }
    records: list[dict[str, Any]] = []

    summary = (
        f"{snapshot.route.get('task_type', '')} {snapshot.route.get('task_level', '')} -> {snapshot.final_status}; "
        f"skills={','.join(selected_skill_names) or 'none'}; bot2={snapshot.verdict.get('status', '') or 'not_required'}"
    ).strip()
    process_content = truncate_content(
        dumps(
            {
                "task": snapshot.task,
                "acceptance": snapshot.acceptance,
                "route": snapshot.route,
                "skill_context": snapshot.skill_context,
                "bot1_result_preview": snapshot.bot1_result[:2000],
                "bot2_verdict": snapshot.verdict,
                "human_message": snapshot.human_message,
            }
        )
    )
    records.append(
        rlm_store.add_record(
            kind="process_summary",
            title=f"{snapshot.route.get('task_type', 'process')} {snapshot.final_status}",
            summary=summary,
            content=process_content,
            tags=base_tags,
            process_id=snapshot.process_id,
            importance=0.85 if snapshot.route.get("risk_level") == "high" else 0.65,
            metadata=metadata,
            store_path=config.store_path,
        )
    )

    if snapshot.bot1_result:
        records.append(
            rlm_store.add_record(
                kind="bot_output",
                title="Bot1 result",
                summary=snapshot.bot1_result.replace("\n", " ")[:240],
                content=truncate_content(snapshot.bot1_result),
                tags=[*base_tags, "bot1"],
                process_id=snapshot.process_id,
                importance=0.7,
                metadata=metadata,
                store_path=config.store_path,
            )
        )

    if snapshot.verdict:
        records.append(
            rlm_store.add_record(
                kind="bot_review",
                title=f"Bot2 verdict {snapshot.verdict.get('status', '')}",
                summary=str(snapshot.verdict.get("summary") or snapshot.verdict.get("status") or "Bot2 verdict"),
                content=truncate_content(dumps(snapshot.verdict)),
                tags=[*base_tags, "bot2", f"bot2/{snapshot.verdict.get('status', '')}"],
                process_id=snapshot.process_id,
                importance=0.78,
                metadata=metadata,
                store_path=config.store_path,
            )
        )

    if snapshot.human_message or snapshot.route.get("human_gate_required"):
        records.append(
            rlm_store.add_record(
                kind="human_gate",
                title=f"Human gate {snapshot.final_status}",
                summary=snapshot.human_message[:240] if snapshot.human_message else "Human gate required by route policy.",
                content=truncate_content(snapshot.human_message or dumps({"route": snapshot.route, "verdict": snapshot.verdict})),
                tags=[*base_tags, "human_gate"],
                process_id=snapshot.process_id,
                importance=0.8,
                metadata=metadata,
                store_path=config.store_path,
            )
        )

    if "hermes-browser" in selected_skill_names:
        records.append(
            rlm_store.add_record(
                kind="skill_usage",
                title="Hermes browser skill selected",
                summary="Authenticated browser skill selected for supplier research/evidence capture.",
                content=truncate_content(dumps(role_skill_context(snapshot.skill_context, "bot1"))),
                tags=[*base_tags, "browser", "skill/hermes-browser"],
                process_id=snapshot.process_id,
                importance=0.72,
                metadata={**metadata, "skill": "hermes-browser"},
                store_path=config.store_path,
            )
        )

    return records


def write_records(
    *,
    args: argparse.Namespace,
    process_id: str,
    supervisor_task_id: str,
    task: str,
    acceptance: str,
    route: dict[str, Any],
    skill_context: dict[str, Any],
    final_status: str,
    bot1_result: str,
    bot2_session_id: str,
    verdict: dict[str, Any],
    report_path: str = "",
    human_message: str = "",
) -> list[dict[str, Any]]:
    return write_process_records(
        ProcessRlmSnapshot(
            process_id=process_id,
            supervisor_task_id=supervisor_task_id,
            task=task,
            acceptance=acceptance,
            route=route,
            skill_context=skill_context,
            final_status=final_status,
            bot1_result=bot1_result,
            bot2_session_id=bot2_session_id,
            verdict=verdict,
            report_path=report_path,
            human_message=human_message,
        ),
        config_from_args(args),
    )


def safe_write_process_records(snapshot: ProcessRlmSnapshot, config: RlmConfig) -> RlmWriteOutcome:
    if not config.enabled:
        return RlmWriteOutcome(status="disabled")
    try:
        records = write_process_records(snapshot, config)
        return RlmWriteOutcome(
            status="ok",
            records=records,
            event_type="rlm_records_written",
            payload=records_event_payload(config, records),
        )
    except Exception as exc:
        return RlmWriteOutcome(
            status="error",
            event_type="rlm_write_failed",
            payload=redact_payload({"error": f"{type(exc).__name__}: {exc}"}),
        )


def records_event_payload(config_or_args: RlmConfig | argparse.Namespace, records: list[dict[str, Any]]) -> dict[str, Any]:
    config = config_or_args if isinstance(config_or_args, RlmConfig) else config_from_args(config_or_args)
    return {
        "record_ids": [record["id"] for record in records],
        "record_kinds": [record["kind"] for record in records],
        "store_path": config.store_path or str(rlm_store.get_store_path()),
    }
