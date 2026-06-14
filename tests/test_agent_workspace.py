from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import agent_workspace  # noqa: E402


def test_workspace_path_uses_env_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HERMES_AGENT_WORKSPACE_ROOT", str(tmp_path))

    assert agent_workspace.workspace_path("proc-1", "agent_A") == tmp_path / "proc-1" / "agent_A"


@pytest.mark.parametrize("bad_id", ["", ".", "..", "../x", "x/../y", "x/y", r"x\y", "safe..nope", "white space"])
def test_safe_ids_reject_traversal_separators_and_empty(bad_id: str) -> None:
    with pytest.raises(ValueError):
        agent_workspace.safe_process_id(bad_id)
    with pytest.raises(ValueError):
        agent_workspace.safe_agent_id(bad_id)


def test_create_workspace_permissions_and_metadata(tmp_path: Path) -> None:
    result = agent_workspace.create_workspace("proc-1", "agent_A", root=tmp_path)
    workspace = Path(result["path"])
    metadata_path = workspace / "metadata.json"

    assert workspace == tmp_path.resolve() / "proc-1" / "agent_A"
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    assert metadata_path.exists()
    assert stat.S_IMODE(metadata_path.stat().st_mode) == 0o600

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["process_id"] == "proc-1"
    assert metadata["agent_id"] == "agent_A"
    assert metadata["mode"] == "isolated_copy_on_write"
    assert metadata["created_at"]


def test_workspace_status_reports_existing_and_missing(tmp_path: Path) -> None:
    agent_workspace.create_workspace("proc-1", "agent_A", root=tmp_path)

    existing = agent_workspace.workspace_status("proc-1", "agent_A", root=tmp_path)
    missing = agent_workspace.workspace_status("proc-1", "missing", root=tmp_path)

    assert existing["exists"] is True
    assert existing["metadata"]["process_id"] == "proc-1"
    assert existing["metadata"]["agent_id"] == "agent_A"
    assert missing["exists"] is False
    assert missing["metadata"] == {}


def test_list_workspaces_returns_metadata_and_ignores_unsafe_names(tmp_path: Path) -> None:
    agent_workspace.create_workspace("proc-1", "agent_A", root=tmp_path)
    agent_workspace.create_workspace("proc-1", "agent_B", root=tmp_path)
    (tmp_path / "bad name").mkdir()

    listing = agent_workspace.list_workspaces(root=tmp_path)

    assert listing["root"] == str(tmp_path.resolve())
    assert [(item["process_id"], item["agent_id"]) for item in listing["workspaces"]] == [
        ("proc-1", "agent_A"),
        ("proc-1", "agent_B"),
    ]
    assert all(item["exists"] is True for item in listing["workspaces"])


def test_cleanup_workspace_removes_only_target_workspace(tmp_path: Path) -> None:
    agent_workspace.create_workspace("proc-1", "agent_A", root=tmp_path)
    sibling = tmp_path / "proc-1" / "agent_B"
    sibling.mkdir(parents=True)

    result = agent_workspace.cleanup_workspace("proc-1", "agent_A", root=tmp_path)

    assert result == {"path": str(tmp_path.resolve() / "proc-1" / "agent_A"), "removed": True}
    assert not (tmp_path / "proc-1" / "agent_A").exists()
    assert sibling.exists()


def test_cleanup_workspace_is_noop_when_missing(tmp_path: Path) -> None:
    result = agent_workspace.cleanup_workspace("proc-1", "agent_A", root=tmp_path)

    assert result == {"path": str(tmp_path.resolve() / "proc-1" / "agent_A"), "removed": False}


def test_cleanup_refuses_symlink_escape_outside_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    process_dir = tmp_path / "proc-1"
    process_dir.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="unsafe"):
        agent_workspace.cleanup_workspace("proc-1", "agent_A", root=tmp_path)

    assert outside.exists()


def test_cleanup_refuses_unsafe_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        agent_workspace.cleanup_workspace("../proc", "agent_A", root=tmp_path)


def test_cli_create_and_cleanup_print_json(tmp_path: Path) -> None:
    create = subprocess.run(
        [sys.executable, str(SCRIPTS / "agent_workspace.py"), "create", "proc-1", "agent_A", "--root", str(tmp_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    create_payload = json.loads(create.stdout)

    assert Path(create_payload["path"]).exists()
    assert create_payload["metadata"]["mode"] == "isolated_copy_on_write"

    cleanup = subprocess.run(
        [sys.executable, str(SCRIPTS / "agent_workspace.py"), "cleanup", "proc-1", "agent_A", "--root", str(tmp_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    cleanup_payload = json.loads(cleanup.stdout)

    assert cleanup_payload["removed"] is True
    assert not Path(create_payload["path"]).exists()


def test_cli_status_and_list_print_json(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPTS / "agent_workspace.py"), "create", "proc-1", "agent_A", "--root", str(tmp_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    status = subprocess.run(
        [sys.executable, str(SCRIPTS / "agent_workspace.py"), "status", "proc-1", "agent_A", "--root", str(tmp_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    listing = subprocess.run(
        [sys.executable, str(SCRIPTS / "agent_workspace.py"), "list", "--root", str(tmp_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert json.loads(status.stdout)["metadata"]["agent_id"] == "agent_A"
    assert json.loads(listing.stdout)["workspaces"][0]["process_id"] == "proc-1"
