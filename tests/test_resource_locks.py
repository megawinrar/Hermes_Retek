"""Characterization tests for supervisor_common resource locks.

Concurrency guard preventing simultaneous deploys/writes. The acquire path must
be all-or-nothing: a conflict rolls back partial acquisitions.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from supervisor_common import (  # noqa: E402
    acquire_resource_locks,
    active_resource_lock,
    release_resource_locks,
)


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    return tmp_path / "store.db"


def test_acquire_then_active(store: Path) -> None:
    acquired = acquire_resource_locks("t1", ["git-write", "runtime-deploy"], reason="deploy", store_path=store)
    assert acquired == ["git-write", "runtime-deploy"]
    lock = active_resource_lock("git-write", store_path=store)
    assert lock is not None and lock["task_id"] == "t1"


def test_conflict_rolls_back_partial_acquire(store: Path) -> None:
    acquire_resource_locks("t1", ["a", "b"], reason="first", store_path=store)
    with pytest.raises(SystemExit):
        acquire_resource_locks("t2", ["b", "c"], reason="second", store_path=store)
    # 'c' must NOT have been acquired because 'b' conflicted (all-or-nothing).
    assert active_resource_lock("c", store_path=store) is None
    # 'a' and 'b' still belong to t1.
    assert active_resource_lock("a", store_path=store)["task_id"] == "t1"
    assert active_resource_lock("b", store_path=store)["task_id"] == "t1"


def test_release_frees_locks(store: Path) -> None:
    acquire_resource_locks("t1", ["a", "b"], reason="first", store_path=store)
    release_resource_locks("t1", ["a"], store_path=store)
    assert active_resource_lock("a", store_path=store) is None
    assert active_resource_lock("b", store_path=store) is not None


def test_same_task_reacquire_same_resource_conflicts(store: Path) -> None:
    # Lock is keyed by resource (PRIMARY KEY); re-acquiring an already-held
    # resource raises even for the same task.
    acquire_resource_locks("t1", ["a"], reason="first", store_path=store)
    with pytest.raises(SystemExit):
        acquire_resource_locks("t1", ["a"], reason="again", store_path=store)


def test_empty_resources_is_noop(store: Path) -> None:
    assert acquire_resource_locks("t1", [], reason="noop", store_path=store) == []
