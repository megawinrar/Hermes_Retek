"""Tests for skill_index.validate_manifest error branches (top-15 #9).

The validator guards that, e.g., a high-risk skill cannot load without the
gateway. None of its error paths were tested before.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from skill_index import validate_manifest  # noqa: E402


def _valid_skill() -> dict[str, Any]:
    return {
        "name": "s1",
        "path": "s1.md",
        "description": "demo",
        "tags": [],
        "worker_roles": [],
        "risk_level": "low",
        "script_presence": "none",
        "network_required": False,
        "auth_required": False,
        "load_policy": "lazy",
        "levels": ["L2"],
    }


def _valid_manifest() -> dict[str, Any]:
    return {
        "version": 1,
        "skills": [_valid_skill()],
        "level_policy": {level: {} for level in ["L0", "L1", "L2", "L3", "L4"]},
        "task_type_tags": {},
    }


@pytest.fixture()
def base_dir(tmp_path: Path) -> Path:
    (tmp_path / "s1.md").write_text("# skill")
    return tmp_path


def test_valid_manifest_passes(base_dir: Path) -> None:
    validate_manifest(_valid_manifest(), base_dir=base_dir)  # must not raise


def test_wrong_version_rejected(base_dir: Path) -> None:
    manifest = _valid_manifest()
    manifest["version"] = 2
    with pytest.raises(ValueError, match="version must be 1"):
        validate_manifest(manifest, base_dir=base_dir)


def test_empty_skills_rejected(base_dir: Path) -> None:
    manifest = _valid_manifest()
    manifest["skills"] = []
    with pytest.raises(ValueError, match="non-empty skills list"):
        validate_manifest(manifest, base_dir=base_dir)


def test_duplicate_name_rejected(base_dir: Path) -> None:
    manifest = _valid_manifest()
    manifest["skills"] = [_valid_skill(), _valid_skill()]
    with pytest.raises(ValueError, match="duplicate skill entry"):
        validate_manifest(manifest, base_dir=base_dir)


def test_missing_required_field_rejected(base_dir: Path) -> None:
    manifest = _valid_manifest()
    del manifest["skills"][0]["tags"]
    with pytest.raises(ValueError, match="missing fields"):
        validate_manifest(manifest, base_dir=base_dir)


def test_nonexistent_path_rejected(base_dir: Path) -> None:
    manifest = _valid_manifest()
    manifest["skills"][0]["path"] = "does_not_exist.md"
    with pytest.raises(ValueError, match="path does not exist"):
        validate_manifest(manifest, base_dir=base_dir)


def test_high_risk_skill_must_require_gateway(base_dir: Path) -> None:
    manifest = _valid_manifest()
    manifest["skills"][0]["risk_level"] = "high"  # no gateway_required
    with pytest.raises(ValueError, match="high-risk skill must require gateway"):
        validate_manifest(manifest, base_dir=base_dir)


def test_high_risk_with_gateway_is_allowed(base_dir: Path) -> None:
    manifest = _valid_manifest()
    manifest["skills"][0]["risk_level"] = "high"
    manifest["skills"][0]["gateway_required"] = True
    validate_manifest(manifest, base_dir=base_dir)  # must not raise


def test_missing_level_policy_rejected(base_dir: Path) -> None:
    manifest = _valid_manifest()
    del manifest["level_policy"]["L4"]
    with pytest.raises(ValueError, match="missing level_policy for L4"):
        validate_manifest(manifest, base_dir=base_dir)
