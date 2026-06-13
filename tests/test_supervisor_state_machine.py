from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from supervisor_common import (  # noqa: E402
    ALLOWED_STATUS_TRANSITIONS,
    SUPERVISOR_STATUSES,
    create_human_escalation,
    create_task,
    get_task,
    link_bot2,
    record_human_decision,
    update_task,
)


def test_status_transition_table_covers_every_status() -> None:
    assert set(ALLOWED_STATUS_TRANSITIONS) == SUPERVISOR_STATUSES
    for destinations in ALLOWED_STATUS_TRANSITIONS.values():
        assert destinations <= SUPERVISOR_STATUSES


def test_failed_task_cannot_be_reopened_as_approved(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Run risky change", store_path=store)["task_id"]
    update_task(task_id, status="running", store_path=store)
    update_task(task_id, status="failed", store_path=store)

    with pytest.raises(SystemExit, match="illegal supervisor transition: failed -> approved"):
        update_task(task_id, status="approved", store_path=store)

    assert get_task(task_id, store_path=store)["status"] == "failed"


def test_created_task_cannot_skip_directly_to_approved(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Skip gates", store_path=store)["task_id"]

    with pytest.raises(SystemExit, match="illegal supervisor transition: created -> approved"):
        update_task(task_id, status="approved", store_path=store)


def test_return_to_bot1_can_restart_running_cycle(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Fix after Bot2 objection", store_path=store)["task_id"]
    update_task(task_id, status="running", bot1_result="Bot1 v1", store_path=store)
    update_task(task_id, status="awaiting_human_decision", store_path=store)
    create_human_escalation(
        get_task(task_id, store_path=store),
        "bot2-reject",
        {"status": "REJECT", "summary": "needs fixes", "risks": [], "required_fixes": ["fix"]},
        store_path=store,
    )
    record_human_decision(task_id, "yes", "Bot2 is right", store_path=store)

    assert get_task(task_id, store_path=store)["status"] == "return_to_bot1"
    update_task(task_id, status="running", store_path=store)
    assert get_task(task_id, store_path=store)["status"] == "running"


def test_loop_guard_blocks_restart_after_three_bot2_cycles(tmp_path: Path) -> None:
    store = tmp_path / "supervisor.db"
    task_id = create_task("Repeated Bot1 Bot2 loop", store_path=store)["task_id"]
    update_task(task_id, status="running", bot1_result="Bot1 v1", store_path=store)

    for index in range(3):
        link_bot2(
            task_id,
            f"bot2-cycle-{index}",
            {"status": "REQUEST_CHANGES", "summary": "needs fixes", "risks": [], "required_fixes": ["fix"]},
            store_path=store,
        )
        update_task(task_id, status="awaiting_human_decision", store_path=store)
        create_human_escalation(
            get_task(task_id, store_path=store),
            f"bot2-cycle-{index}",
            {"status": "REQUEST_CHANGES", "summary": "needs fixes", "risks": [], "required_fixes": ["fix"]},
            store_path=store,
        )
        record_human_decision(task_id, "yes", "try another Bot1 pass", store_path=store)
        if index < 2:
            update_task(task_id, status="running", store_path=store)

    with pytest.raises(SystemExit, match="bot loop guard blocked restart after 3 Bot#2 cycles"):
        update_task(task_id, status="running", store_path=store)

    assert get_task(task_id, store_path=store)["status"] == "blocked"
