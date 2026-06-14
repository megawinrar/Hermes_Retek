#!/usr/bin/env python3
"""Isolated agent workspace helper for Hermes processes."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_WORKSPACE_ROOT = "/opt/data/agent_workspaces"
WORKSPACE_MODE = "isolated_copy_on_write"
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def workspace_root(root: str | os.PathLike[str] | None = None) -> Path:
    """Return the configured workspace root."""
    selected = root if root is not None else os.environ.get("HERMES_AGENT_WORKSPACE_ROOT", DEFAULT_WORKSPACE_ROOT)
    return Path(selected).expanduser().resolve(strict=False)


def _safe_id(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if value == "":
        raise ValueError(f"{label} must not be empty")
    if value in {".", ".."} or ".." in value:
        raise ValueError(f"{label} must not contain traversal")
    if "/" in value or "\\" in value:
        raise ValueError(f"{label} must not contain path separators")
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(f"{label} contains unsafe characters")
    return value


def safe_agent_id(agent_id: str) -> str:
    """Validate and return an agent id safe for use as one path component."""
    return _safe_id(agent_id, "agent_id")


def safe_process_id(process_id: str) -> str:
    """Validate and return a process id safe for use as one path component."""
    return _safe_id(process_id, "process_id")


def workspace_path(process_id: str, agent_id: str, root: str | os.PathLike[str] | None = None) -> Path:
    """Return the isolated workspace path for an agent in a process."""
    base = workspace_root(root)
    return base / safe_process_id(process_id) / safe_agent_id(agent_id)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _metadata(process_id: str, agent_id: str) -> dict[str, str]:
    return {
        "process_id": process_id,
        "agent_id": agent_id,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": WORKSPACE_MODE,
    }


def create_workspace(
    process_id: str,
    agent_id: str,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Create an isolated 0700 workspace and write metadata.json."""
    safe_process = safe_process_id(process_id)
    safe_agent = safe_agent_id(agent_id)
    base = workspace_root(root)
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve(strict=True)
    process_path = base / safe_process
    path = process_path / safe_agent

    if process_path.is_symlink():
        raise ValueError("refusing to use symlink process workspace path")
    process_path.mkdir(mode=0o700, exist_ok=True)

    if path.is_symlink():
        raise ValueError("refusing to use symlink workspace path")
    path.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)

    resolved_path = path.resolve(strict=True)
    if resolved_path == base or not _is_relative_to(resolved_path, base):
        raise ValueError("workspace path resolved outside root")

    metadata = _metadata(safe_process, safe_agent)
    metadata_path = path / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(metadata_path, stat.S_IRUSR | stat.S_IWUSR)

    return {
        "path": str(path),
        "metadata_path": str(metadata_path),
        "metadata": metadata,
    }


def workspace_status(
    process_id: str,
    agent_id: str,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Return status for one workspace without creating it."""
    base = workspace_root(root)
    path = workspace_path(process_id, agent_id, base)
    if path == base:
        raise ValueError("workspace path must not be root")
    if path.is_symlink():
        raise ValueError("refusing to inspect symlink workspace path")
    if not path.exists():
        return {"path": str(path), "exists": False, "metadata": {}}

    resolved_path = path.resolve(strict=True)
    if resolved_path == base or not _is_relative_to(resolved_path, base):
        raise ValueError("workspace path resolved outside root")

    metadata_path = path / "metadata.json"
    metadata: dict[str, Any] = {}
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    return {
        "path": str(path),
        "exists": True,
        "metadata_path": str(metadata_path),
        "metadata": metadata,
    }


def list_workspaces(root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """List existing process/agent workspaces under root."""
    base = workspace_root(root)
    if not base.exists():
        return {"root": str(base), "workspaces": []}
    if base.is_symlink():
        raise ValueError("refusing to list symlink workspace root")

    workspaces: list[dict[str, Any]] = []
    for process_path in sorted(path for path in base.iterdir() if path.is_dir() and not path.is_symlink()):
        try:
            process_id = safe_process_id(process_path.name)
        except (TypeError, ValueError):
            continue
        for agent_path in sorted(path for path in process_path.iterdir() if path.is_dir() and not path.is_symlink()):
            try:
                agent_id = safe_agent_id(agent_path.name)
            except (TypeError, ValueError):
                continue
            status = workspace_status(process_id, agent_id, base)
            status["process_id"] = process_id
            status["agent_id"] = agent_id
            workspaces.append(status)
    return {"root": str(base), "workspaces": workspaces}


def cleanup_workspace(
    process_id: str,
    agent_id: str,
    root: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Remove one isolated workspace, refusing root and escaped paths."""
    safe_process = safe_process_id(process_id)
    safe_agent = safe_agent_id(agent_id)
    base = workspace_root(root)
    path = workspace_path(safe_process, safe_agent, base)

    if path == base:
        raise ValueError("refusing to remove workspace root")

    if path.parent.exists() or path.parent.is_symlink():
        resolved_parent = path.parent.resolve(strict=True)
        candidate = resolved_parent / path.name
        if candidate == base or not _is_relative_to(candidate, base):
            raise ValueError("refusing to remove unsafe workspace path")
        if path.parent.is_symlink():
            raise ValueError("refusing to remove symlink process workspace path")

    if not path.exists() and not path.is_symlink():
        return {"path": str(path), "removed": False}

    resolved_path = path.resolve(strict=True)
    if resolved_path == base or not _is_relative_to(resolved_path, base):
        raise ValueError("refusing to remove unsafe workspace path")
    if path.is_symlink():
        raise ValueError("refusing to remove symlink workspace path")

    shutil.rmtree(path)
    return {"path": str(path), "removed": True}


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes isolated agent workspace helper")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create an isolated workspace")
    create.add_argument("process_id")
    create.add_argument("agent_id")
    create.add_argument("--root", default=None)
    create.set_defaults(func=lambda args: create_workspace(args.process_id, args.agent_id, args.root))

    status = sub.add_parser("status", help="Inspect an isolated workspace")
    status.add_argument("process_id")
    status.add_argument("agent_id")
    status.add_argument("--root", default=None)
    status.set_defaults(func=lambda args: workspace_status(args.process_id, args.agent_id, args.root))

    list_cmd = sub.add_parser("list", help="List isolated workspaces")
    list_cmd.add_argument("--root", default=None)
    list_cmd.set_defaults(func=lambda args: list_workspaces(args.root))

    cleanup = sub.add_parser("cleanup", help="Remove an isolated workspace")
    cleanup.add_argument("process_id")
    cleanup.add_argument("agent_id")
    cleanup.add_argument("--root", default=None)
    cleanup.set_defaults(func=lambda args: cleanup_workspace(args.process_id, args.agent_id, args.root))

    return parser


def main() -> None:
    args = build_parser().parse_args()
    _print_json(args.func(args))


if __name__ == "__main__":
    main()
