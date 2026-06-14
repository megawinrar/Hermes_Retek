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
    assert metadata["status"] == "created"
    assert metadata["merge_owner"] == "supervisor"
    assert metadata["auto_merge"] is False
    assert metadata["created_at"]
    assert metadata["updated_at"]


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


def test_workspace_lifecycle_status_accept_and_discard_are_supervisor_gated(tmp_path: Path) -> None:
    agent_workspace.create_workspace("proc-1", "bot1", root=tmp_path)

    running = agent_workspace.set_workspace_status("proc-1", "bot1", "running", root=tmp_path, reason="Bot1 started")
    assert running["metadata"]["status"] == "running"
    assert running["metadata"]["status_reason"] == "Bot1 started"
    assert running["metadata"]["auto_merge"] is False

    with pytest.raises(ValueError, match="supervisor approval"):
        agent_workspace.accept_workspace("proc-1", "bot1", root=tmp_path, bot2_status="APPROVE")
    with pytest.raises(ValueError, match="Bot2 approval"):
        agent_workspace.accept_workspace("proc-1", "bot1", root=tmp_path, supervisor_approved=True, bot2_status="REQUEST_CHANGES")

    accepted = agent_workspace.accept_workspace(
        "proc-1",
        "bot1",
        root=tmp_path,
        supervisor_approved=True,
        bot2_status="APPROVE_WITH_EVIDENCE",
        reason="Evidence checked",
    )
    assert accepted["metadata"]["status"] == "accepted"
    assert accepted["metadata"]["decision"]["action"] == "accept"
    assert accepted["metadata"]["decision"]["merge_owner"] == "supervisor"
    assert accepted["metadata"]["decision"]["auto_merge"] is False

    discarded = agent_workspace.discard_workspace("proc-1", "bot1", root=tmp_path, reason="Superseded by safer patch")
    assert discarded["metadata"]["status"] == "discarded"
    assert discarded["metadata"]["decision"]["action"] == "discard"
    assert discarded["metadata"]["decision"]["cleanup_allowed"] is True


def test_workspace_lifecycle_metadata_redacts_secret_like_values(tmp_path: Path) -> None:
    secret = "tok_" + "J" * 32
    agent_workspace.create_workspace("proc-1", "bot1", root=tmp_path)

    status = agent_workspace.set_workspace_status("proc-1", "bot1", "completed", root=tmp_path, reason=f"used {secret}")

    raw = Path(status["metadata_path"]).read_text(encoding="utf-8")
    assert secret not in raw
    assert "[REDACTED]" in raw


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


def test_cli_accept_and_discard_update_lifecycle_without_cleanup(tmp_path: Path) -> None:
    subprocess.run(
        [sys.executable, str(SCRIPTS / "agent_workspace.py"), "create", "proc-1", "agent_A", "--root", str(tmp_path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    accepted = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "agent_workspace.py"),
            "accept",
            "proc-1",
            "agent_A",
            "--root",
            str(tmp_path),
            "--supervisor-approved",
            "--bot2-status",
            "APPROVE",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    accepted_payload = json.loads(accepted.stdout)
    assert accepted_payload["metadata"]["status"] == "accepted"
    assert accepted_payload["metadata"]["decision"]["auto_merge"] is False

    discarded = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "agent_workspace.py"),
            "discard",
            "proc-1",
            "agent_A",
            "--root",
            str(tmp_path),
            "--reason",
            "not needed",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    discarded_payload = json.loads(discarded.stdout)
    assert discarded_payload["metadata"]["status"] == "discarded"
    assert Path(discarded_payload["path"]).exists()
