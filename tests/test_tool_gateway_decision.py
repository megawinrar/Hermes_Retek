"""Characterization tests for tool_gateway approval/decision (DB-backed).

Pin the fail-closed approval logic and the defensive `except SystemExit`
branch around add_event.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

import tool_gateway  # noqa: E402
from tool_gateway import approval_decision, gateway_decision  # noqa: E402
from supervisor_common import create_task, link_bot2, update_task  # noqa: E402


def _approve(task_id: str, store: Path) -> None:
    update_task(task_id, status="running", store_path=store)
    update_task(task_id, status="approved", store_path=store)
    link_bot2(
        task_id,
        "sess-1",
        {"status": "APPROVE", "approved_action": "execute", "summary": "ok"},
        store_path=store,
    )


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    return tmp_path / "store.db"


def test_non_dangerous_command_allowed_without_task(store: Path) -> None:
    decision = approval_decision(
        task_id="", classification={"dangerous": False}, store_path=store
    )
    assert decision == {"allowed": True, "reason": "command_not_dangerous"}


def test_dangerous_command_requires_task_id(store: Path) -> None:
    decision = approval_decision(
        task_id="", classification={"dangerous": True, "risks": ["git_push"]}, store_path=store
    )
    assert decision == {"allowed": False, "reason": "missing_supervisor_task_id"}


def test_dangerous_command_denied_when_task_not_approved(store: Path) -> None:
    created = create_task("push something", store_path=store)
    decision = gateway_decision(
        task_id=created["task_id"], argv=["git", "push"], store_path=store
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "supervisor_task_not_approved"


def test_dangerous_command_allowed_with_linked_bot2_execute(store: Path) -> None:
    created = create_task("push approved change", store_path=store)
    _approve(created["task_id"], store)
    decision = gateway_decision(
        task_id=created["task_id"], argv=["git", "push"], store_path=store
    )
    assert decision["allowed"] is True
    assert decision["reason"] == "linked_bot2_approval_to_execute"
    assert decision["resources"] == ["git-write"]


def test_dangerous_command_nonexistent_task_raises(store: Path) -> None:
    # approval_decision -> get_task raises SystemExit for an unknown task id.
    with pytest.raises(SystemExit):
        gateway_decision(task_id="sup-does-not-exist", argv=["git", "push"], store_path=store)


def test_fail_closed_when_add_event_raises(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    created = create_task("push something", store_path=store)

    def _boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise SystemExit("event store unavailable")

    monkeypatch.setattr(tool_gateway, "add_event", _boom)
    decision = gateway_decision(
        task_id=created["task_id"], argv=["git", "push"], store_path=store
    )
    assert decision["allowed"] is False
    assert decision["reason"] == "supervisor_task_not_found"
