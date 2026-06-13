#!/usr/bin/env python3
"""Read the Hermes skill manifest and select role/level skills lazily."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "skills" / "manifest.json"


def manifest_path(path: str | None = None) -> Path:
    return Path(path or os.environ.get("HERMES_SKILL_MANIFEST", DEFAULT_MANIFEST))


def load_manifest(path: str | Path | None = None) -> dict[str, Any]:
    target = manifest_path(str(path) if path else None)
    data = json.loads(target.read_text(encoding="utf-8"))
    validate_manifest(data, base_dir=target.parents[1])
    return data


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


def skill_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["name"]): item for item in manifest["skills"]}


def select_skills(
    manifest: dict[str, Any],
    *,
    level: str,
    role: str | None = None,
    include_approval_required: bool = False,
) -> list[dict[str, Any]]:
    policy = (manifest.get("level_policy") or {}).get(level)
    if not policy:
        raise ValueError(f"unknown level: {level}")
    allowed_roles = set(policy.get("allowed_roles") or [])
    if role and role not in allowed_roles:
        return []
    forbidden_tags = set(policy.get("forbidden_tags") or [])
    autoload = set(policy.get("autoload_skills") or [])
    approval_only = set(policy.get("approval_only_skills") or [])

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
        if name in autoload or role:
            selected.append(item)
    return selected


def as_output(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": item["name"],
            "path": item["path"],
            "description": item["description"],
            "worker_roles": item["worker_roles"],
            "risk_level": item["risk_level"],
            "load_policy": item["load_policy"],
            "gateway_required": bool(item.get("gateway_required")),
        }
        for item in items
    ]


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
    )
    print(json.dumps(as_output(selected), ensure_ascii=False, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes skill manifest selector")
    parser.add_argument("--manifest", default="")
    sub = parser.add_subparsers(dest="cmd", required=True)

    list_cmd = sub.add_parser("list")
    list_cmd.set_defaults(func=cmd_list)

    select = sub.add_parser("select")
    select.add_argument("--level", required=True, choices=["L0", "L1", "L2", "L3", "L4"])
    select.add_argument("--role", default="")
    select.add_argument("--include-approval-required", action="store_true")
    select.set_defaults(func=cmd_select)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
