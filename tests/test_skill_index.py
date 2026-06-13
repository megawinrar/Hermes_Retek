from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from skill_index import load_manifest, select_skills  # noqa: E402


def names(items: list[dict[str, object]]) -> set[str]:
    return {str(item["name"]) for item in items}


def test_skill_manifest_validates_and_paths_exist() -> None:
    manifest = load_manifest()
    assert manifest["version"] == 1
    for item in manifest["skills"]:
        assert (ROOT / item["path"]).exists()


def test_l0_and_l1_do_not_load_heavy_devops_or_github_skills() -> None:
    manifest = load_manifest()
    assert select_skills(manifest, level="L0") == []
    l1 = select_skills(manifest, level="L1")
    assert names(l1) == {"hermes-developer"}
    assert all(not item.get("gateway_required") for item in l1)


def test_l4_devops_requires_explicit_approval_inclusion() -> None:
    manifest = load_manifest()
    devops_without_approval = select_skills(manifest, level="L4", role="devops")
    assert devops_without_approval == []
    devops_with_approval = select_skills(
        manifest,
        level="L4",
        role="devops",
        include_approval_required=True,
    )
    assert names(devops_with_approval) == {"hermes-devops", "github-pr-workflow"}
    assert all(item.get("gateway_required") for item in devops_with_approval)


def test_skill_index_cli_select_outputs_json() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "skill_index.py"),
            "select",
            "--level",
            "L3",
            "--role",
            "architect",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert names(payload) == {"hermes-architect"}
