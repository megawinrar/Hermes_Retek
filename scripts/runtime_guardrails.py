#!/usr/bin/env python3
"""Apply Hermes runtime guardrails to a config mapping or config.yaml file."""

from __future__ import annotations

import argparse
import copy
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_GUARDRAILS: dict[str, Any] = {
    "agent": {
        "max_turns": 16,
        "gateway_notify_interval": 30,
        "gateway_timeout_warning": 300,
    },
    "delegation": {
        "max_concurrent_children": 2,
        "child_timeout_seconds": 120,
        "max_iterations": 8,
    },
    "streaming": {
        "enabled": True,
        "transport": "auto",
        "edit_interval": 1.0,
        "buffer_threshold": 24,
    },
}


def apply_runtime_guardrails(config: dict[str, Any], guardrails: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a copy of config with bounded runtime settings applied."""
    result = copy.deepcopy(config)
    selected = guardrails or DEFAULT_GUARDRAILS
    for section, values in selected.items():
        section_map = result.setdefault(section, {})
        if not isinstance(section_map, dict):
            section_map = {}
            result[section] = section_map
        section_map.update(values)
    return result


def _load_yaml_module():
    try:
        import yaml  # type: ignore
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on runtime image
        raise SystemExit("PyYAML is required for file mode; run inside Hermes runtime venv") from exc
    return yaml


def backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return path.with_name(f"{path.name}.backup-runtime-guardrails-{stamp}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Path to Hermes config.yaml")
    parser.add_argument("--no-backup", action="store_true", help="Do not write a timestamped backup")
    args = parser.parse_args()

    yaml = _load_yaml_module()
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise SystemExit("config root must be a mapping")

    updated = apply_runtime_guardrails(config)
    if not args.no_backup:
        shutil.copy2(args.config, backup_path(args.config))
    args.config.write_text(yaml.safe_dump(updated, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
