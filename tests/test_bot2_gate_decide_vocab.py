"""BUG-2 before/after: bot2_gate.cmd_decide records canonical task statuses.

Previously the review store used its own vocabulary (user_agreed_with_bot2 /
user_accepted_bot1). After the fix it records the same statuses the supervisor
task store uses, so both subsystems speak one language.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

import bot2_gate  # noqa: E402
from supervisor_common import (  # noqa: E402
    HUMAN_DECISION_NO_STATUS,
    HUMAN_DECISION_YES_STATUS,
)


def _session_status(store: Path, sid: str) -> str:
    with bot2_gate.db(store) as con:
        row = con.execute("SELECT status FROM bot2_review_sessions WHERE id=?", (sid,)).fetchone()
    return row["status"]


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(bot2_gate, "send_telegram", lambda *args, **kwargs: False)
    return tmp_path / "bot2_gate.db"


def test_decide_yes_writes_canonical_return_to_bot1(store: Path, capsys) -> None:
    sid = bot2_gate.create_session("manual", "task", "acceptance", store_path=store)
    bot2_gate.cmd_decide(SimpleNamespace(session_id=sid, choice="yes", reason="", store=store))

    assert HUMAN_DECISION_YES_STATUS == "return_to_bot1"
    assert _session_status(store, sid) == HUMAN_DECISION_YES_STATUS
    assert json.loads(capsys.readouterr().out)["status"] == HUMAN_DECISION_YES_STATUS


def test_decide_no_writes_canonical_user_override(store: Path, capsys) -> None:
    sid = bot2_gate.create_session("manual", "task", "acceptance", store_path=store)
    bot2_gate.cmd_decide(SimpleNamespace(session_id=sid, choice="no", reason="ok", store=store))

    assert HUMAN_DECISION_NO_STATUS == "accepted_by_user_override"
    assert _session_status(store, sid) == HUMAN_DECISION_NO_STATUS
    assert json.loads(capsys.readouterr().out)["status"] == HUMAN_DECISION_NO_STATUS


def test_old_vocabulary_is_gone(store: Path) -> None:
    sid = bot2_gate.create_session("manual", "task", "acceptance", store_path=store)
    bot2_gate.cmd_decide(SimpleNamespace(session_id=sid, choice="yes", reason="", store=store))
    assert _session_status(store, sid) not in {"user_agreed_with_bot2", "user_accepted_bot1"}
