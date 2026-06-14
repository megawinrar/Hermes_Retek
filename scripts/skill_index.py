#!/usr/bin/env python3
"""Read the Hermes skill manifest and select role/level skills lazily."""

from __future__ import annotations

import argparse
import copy
import json
import os
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "skills" / "manifest.json"
ROLE_ALIASES = {
    "bot2_light_if_risky": "bot2",
    "devops_if_approved": "devops",
}
_MANIFEST_CACHE: dict[str, tuple[int, int, float, dict[str, Any]]] = {}
_CONTEXT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_enabled() -> bool:
    return os.environ.get("HERMES_SKILL_INDEX_CACHE", "1").strip().lower() not in {"0", "false", "no", "off"}


def _cache_ttl_seconds() -> int:
    raw = os.environ.get("HERMES_SKILL_INDEX_CACHE_TTL_SECONDS", "300")
    try:
        return max(0, int(raw))
    except ValueError:
        return 300


def _context_cache_size() -> int:
    raw = os.environ.get("HERMES_SKILL_INDEX_CONTEXT_CACHE_SIZE", "256")
    try:
        return max(0, int(raw))
    except ValueError:
        return 256


def clear_caches() -> None:
    _MANIFEST_CACHE.clear()
    _CONTEXT_CACHE.clear()


def cache_stats() -> dict[str, int]:
    return {
        "manifest_entries": len(_MANIFEST_CACHE),
        "context_entries": len(_CONTEXT_CACHE),
    }


def manifest_path(path: str | None = None) -> Path:
    return Path(path or os.environ.get("HERMES_SKILL_MANIFEST", DEFAULT_MANIFEST))


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    target = manifest_path(str(path) if path else None)
    if _cache_enabled():
        stat = target.stat()
        cache_key = str(target.resolve())
        cached = _MANIFEST_CACHE.get(cache_key)
        now = time.monotonic()
        if cached:
            cached_mtime_ns, cached_size, expires_at, cached_data = cached
            if cached_mtime_ns == stat.st_mtime_ns and cached_size == stat.st_size and now <= expires_at:
                return copy.deepcopy(cached_data)

    data = json.loads(target.read_text(encoding="utf-8"))
    validate_manifest(data, base_dir=target.parents[1])
    data["_manifest_path"] = str(target)
    if _cache_enabled():
        stat = target.stat()
        _MANIFEST_CACHE[str(target.resolve())] = (
            stat.st_mtime_ns,
            stat.st_size,
            time.monotonic() + _cache_ttl_seconds(),
            copy.deepcopy(data),
        )
    return copy.deepcopy(data)


def validate_manifest(data: dict[str, Any], *, base_dir: Path = ROOT) -> None:
    names: set[str] = set()
    if data.get("version") != 1:
        raise ValueError("skill manifest version must be 1")
    skills = data.get("skills")
    if not isinstance(skills, list) or not skills:
        raise ValueError("skill manifest must contain non-empty skills list")
    for item in skills:
        name = str(item.get("name") or "")
        if not name:
            raise ValueError("skill entry missing name")
        if name in names:
            raise ValueError(f"duplicate skill entry: {name}")
        names.add(name)
        required = [
            "path",
            "description",
            "tags",
            "worker_roles",
            "risk_level",
            "script_presence",
            "network_required",
            "auth_required",
            "load_policy",
            "levels",
        ]
        missing = [field for field in required if field not in item]
        if missing:
            raise ValueError(f"{name} missing fields: {', '.join(missing)}")
        skill_path = base_dir / str(item["path"])
        if not skill_path.exists():
            raise ValueError(f"{name} path does not exist: {item['path']}")
        if item.get("risk_level") == "high" and not item.get("gateway_required"):
            raise ValueError(f"{name} high-risk skill must require gateway")
    level_policy = data.get("level_policy") or {}
    for level in ["L0", "L1", "L2", "L3", "L4"]:
        if level not in level_policy:
            raise ValueError(f"missing level_policy for {level}")
    task_type_tags = data.get("task_type_tags") or {}
    if not isinstance(task_type_tags, dict):
        raise ValueError("task_type_tags must be an object when present")
    for task_type, tags in task_type_tags.items():
        if not isinstance(task_type, str) or not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError("task_type_tags values must be string lists")


def skill_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["name"]): item for item in manifest["skills"]}


def normalize_role(role: str) -> str:
    return ROLE_ALIASES.get(role, role)


def task_tags(manifest: dict[str, Any], task_type: str) -> list[str]:
    tags = (manifest.get("task_type_tags") or {}).get(task_type) or []
    return sorted({str(tag) for tag in tags})


def route_worker_roles(route: dict[str, Any]) -> list[str]:
    roles: list[str] = []
    include_conditional_bot2 = bool(
        route.get("review_required")
        or route.get("human_gate_required")
        or route.get("risk_level") == "high"
        or route.get("task_level") in {"L3", "L4"}
    )
    for raw_role in route.get("process_plan") or []:
        role = str(raw_role)
        if role == "bot2_light_if_risky" and not include_conditional_bot2:
            continue
        normalized = normalize_role(role)
        if normalized not in roles:
            roles.append(normalized)
    return roles


def select_skills(
    manifest: dict[str, Any],
    *,
    level: str,
    role: str | None = None,
    include_approval_required: bool = False,
    preferred_tags: list[str] | None = None,
) -> list[dict[str, Any]]:
    policy = (manifest.get("level_policy") or {}).get(level)
    if not policy:
        raise ValueError(f"unknown level: {level}")
    role = normalize_role(role) if role else None
    allowed_roles = set(policy.get("allowed_roles") or [])
    if role and role not in allowed_roles:
        return []
    forbidden_tags = set(policy.get("forbidden_tags") or [])
    autoload = set(policy.get("autoload_skills") or [])
    approval_only = set(policy.get("approval_only_skills") or [])
    preferred = set(preferred_tags or [])

    selected: list[dict[str, Any]] = []
    for item in manifest["skills"]:
        name = str(item["name"])
        roles = set(item.get("worker_roles") or [])
        tags = set(item.get("tags") or [])
        if role and role not in roles:
            continue
        if not role and roles.isdisjoint(allowed_roles):
            continue
        if level not in set(item.get("levels") or []):
            continue
        if forbidden_tags.intersection(tags):
            continue
        if name in approval_only and not include_approval_required:
            continue
        if item.get("load_policy") == "approval_required" and not include_approval_required:
            continue
        if item.get("load_policy") == "on_demand" and name not in autoload and not tags.intersection(preferred):
            continue
        if name in autoload or role:
            selected.append(item)
    return selected


def as_output(items: list[dict[str, Any]], *, preferred_tags: list[str] | None = None) -> list[dict[str, Any]]:
    preferred = set(preferred_tags or [])
    return [
        {
            "name": item["name"],
            "path": item["path"],
            "description": item["description"],
            "tags": item["tags"],
            "matched_tags": sorted(preferred.intersection(set(item.get("tags") or []))),
            "worker_roles": item["worker_roles"],
            "risk_level": item["risk_level"],
            "script_presence": bool(item.get("script_presence")),
            "network_required": bool(item.get("network_required")),
            "auth_required": bool(item.get("auth_required")),
            "load_policy": item["load_policy"],
            "gateway_required": bool(item.get("gateway_required")),
        }
        for item in items
    ]


def unique_skill_records(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for item in items:
        name = str(item.get("name") or "")
        if name in seen:
            continue
        seen.add(name)
        unique.append(item)
    return unique


def approval_gated_items(
    manifest: dict[str, Any],
    *,
    level: str,
    role: str,
    active_names: set[str],
) -> list[dict[str, Any]]:
    policy = (manifest.get("level_policy") or {}).get(level) or {}
    approval_only = set(policy.get("approval_only_skills") or [])
    gated: list[dict[str, Any]] = []
    for item in select_skills(manifest, level=level, role=role, include_approval_required=True):
        name = str(item["name"])
        if name in active_names:
            continue
        if name in approval_only or item.get("load_policy") == "approval_required":
            gated.append(item)
    return gated


def _context_cache_key(
    manifest: dict[str, Any],
    *,
    route: dict[str, Any],
    include_approval_required: bool,
) -> str:
    manifest_path_value = str(manifest.get("_manifest_path") or manifest_path())
    try:
        stat = Path(manifest_path_value).stat()
        manifest_signature: Any = [manifest_path_value, stat.st_mtime_ns, stat.st_size]
    except OSError:
        manifest_signature = [manifest_path_value, manifest.get("version"), len(manifest.get("skills") or [])]
    route_signature = {
        "task_level": route.get("task_level", ""),
        "task_type": route.get("task_type", ""),
        "risk_level": route.get("risk_level", ""),
        "review_required": bool(route.get("review_required")),
        "human_gate_required": bool(route.get("human_gate_required")),
        "process_plan": [str(item) for item in route.get("process_plan") or []],
        "include_approval_required": bool(include_approval_required),
        "manifest": manifest_signature,
    }
    return json.dumps(route_signature, ensure_ascii=False, sort_keys=True)


def _remember_context(cache_key: str, context: dict[str, Any]) -> None:
    max_size = _context_cache_size()
    if max_size <= 0:
        return
    while len(_CONTEXT_CACHE) >= max_size:
        _CONTEXT_CACHE.pop(next(iter(_CONTEXT_CACHE)))
    _CONTEXT_CACHE[cache_key] = (time.monotonic() + _cache_ttl_seconds(), copy.deepcopy(context))


def _select_skill_context_uncached(
    manifest: dict[str, Any],
    *,
    route: dict[str, Any],
    include_approval_required: bool = False,
) -> dict[str, Any]:
    level = str(route.get("task_level") or "")
    task_type = str(route.get("task_type") or "")
    preferred_tags = task_tags(manifest, task_type)
    roles = route_worker_roles(route)

    by_role: dict[str, list[dict[str, Any]]] = {}
    gated_by_role: dict[str, list[dict[str, Any]]] = {}
    selected_records: list[dict[str, Any]] = []
    gated_records: list[dict[str, Any]] = []

    for role in roles:
        selected = as_output(
            select_skills(
                manifest,
                level=level,
                role=role,
                include_approval_required=include_approval_required,
                preferred_tags=preferred_tags,
            ),
            preferred_tags=preferred_tags,
        )
        if selected:
            by_role[role] = selected
            selected_records.extend(selected)

        active_names = {str(item["name"]) for item in selected}
        gated = as_output(
            approval_gated_items(manifest, level=level, role=role, active_names=active_names),
            preferred_tags=preferred_tags,
        )
        if gated:
            gated_by_role[role] = gated
            gated_records.extend(gated)

    return {
        "version": 1,
        "manifest": {
            "name": manifest.get("name", ""),
            "version": manifest.get("version"),
            "path": str(manifest.get("_manifest_path") or manifest_path()),
        },
        "status": "selected",
        "selection_policy": "lazy_by_task_level_role_and_tags",
        "task_level": level,
        "task_type": task_type,
        "task_tags": preferred_tags,
        "risk_level": route.get("risk_level", ""),
        "roles": by_role,
        "selected_skills": unique_skill_records(selected_records),
        "gated_roles": gated_by_role,
        "gated_skills": unique_skill_records(gated_records),
        "runtime_contract": {
            "load_only_selected_skill_paths": True,
            "do_not_load_full_skills_tree": True,
            "approval_required_skills_are_gated": True,
            "skill_scripts_require_tool_gateway": bool((manifest.get("default_policy") or {}).get("scripts_require_gateway", True)),
        },
    }


def select_skill_context(
    manifest: dict[str, Any],
    *,
    route: dict[str, Any],
    include_approval_required: bool = False,
) -> dict[str, Any]:
    if not _cache_enabled():
        return _select_skill_context_uncached(
            manifest,
            route=route,
            include_approval_required=include_approval_required,
        )

    cache_key = _context_cache_key(
        manifest,
        route=route,
        include_approval_required=include_approval_required,
    )
    cached = _CONTEXT_CACHE.get(cache_key)
    now = time.monotonic()
    if cached:
        expires_at, context = cached
        if now <= expires_at:
            return copy.deepcopy(context)
        _CONTEXT_CACHE.pop(cache_key, None)

    context = _select_skill_context_uncached(
        manifest,
        route=route,
        include_approval_required=include_approval_required,
    )
    _remember_context(cache_key, context)
    return copy.deepcopy(context)


def cmd_list(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    print(json.dumps(as_output(manifest["skills"]), ensure_ascii=False, indent=2, sort_keys=True))


def cmd_select(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    selected = select_skills(
        manifest,
        level=args.level,
        role=args.role or None,
        include_approval_required=args.include_approval_required,
        preferred_tags=task_tags(manifest, args.task_type),
    )
    print(json.dumps(as_output(selected, preferred_tags=task_tags(manifest, args.task_type)), ensure_ascii=False, indent=2, sort_keys=True))


def cmd_context(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    if args.route_json:
        route = json.loads(args.route_json)
        if not isinstance(route, dict):
            raise SystemExit("--route-json must be a JSON object")
    else:
        route = {
            "task_level": args.level,
            "task_type": args.task_type,
            "risk_level": args.risk_level,
            "review_required": args.review_required,
            "human_gate_required": args.human_gate_required,
            "process_plan": args.process_plan,
        }
    print(
        json.dumps(
            select_skill_context(
                manifest,
                route=route,
                include_approval_required=args.include_approval_required,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes skill manifest selector")
    parser.add_argument("--manifest", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.set_defaults(func=cmd_list)

    select = sub.add_parser("select")
    select.add_argument("--level", required=True, choices=["L0", "L1", "L2", "L3", "L4"])
    select.add_argument("--role", default="")
    select.add_argument("--task-type", default="")
    select.add_argument("--include-approval-required", action="store_true")
    select.set_defaults(func=cmd_select)

    context = sub.add_parser("context")
    context.add_argument("--route-json", default="")
    context.add_argument("--level", default="L2", choices=["L0", "L1", "L2", "L3", "L4"])
    context.add_argument("--task-type", default="standard_task")
    context.add_argument("--risk-level", default="medium", choices=["low", "medium", "high"])
    context.add_argument("--review-required", action="store_true")
    context.add_argument("--human-gate-required", action="store_true")
    context.add_argument("--process-plan", nargs="*", default=["router", "supervisor", "bot1"])
    context.add_argument("--include-approval-required", action="store_true")
    context.set_defaults(func=cmd_context)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
