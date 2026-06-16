from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import process_log  # noqa: E402
import process_orchestrator  # noqa: E402


def test_process_log_writes_jsonl_and_redacts(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "events.jsonl"

    result = process_log.log_event(
        "process_start",
        {"token": "sk_" + "x" * 48, "message": "ok"},
        process_id="proc-1",
        path=path,
    )

    assert result["ok"] is True
    event = json.loads(path.read_text(encoding="utf-8"))
    assert event["event_type"] == "process_start"
    assert event["process_id"] == "proc-1"
    assert event["payload"]["message"] == "ok"
    assert "[REDACTED]" in json.dumps(event, ensure_ascii=False)
    assert "sk_" + "x" * 48 not in json.dumps(event, ensure_ascii=False)


def test_process_log_legacy_cli_still_writes_event(tmp_path: Path, capsys) -> None:
    path = tmp_path / "events.jsonl"

    rc = process_log.main(["process_start", "--process-id", "proc-legacy", "--path", str(path)])

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    event = json.loads(path.read_text(encoding="utf-8"))
    assert out["ok"] is True
    assert event["event_type"] == "process_start"
    assert event["process_id"] == "proc-legacy"


def _parser_route() -> dict:
    return process_orchestrator.classify_task("Спарси Контур по Д16Т и сохрани результаты в Excel")


def _create_process(tmp_path: Path, *, route: dict | None = None, status: str = "running") -> tuple[Path, Path, str]:
    process_store = tmp_path / "process.db"
    supervisor_store = tmp_path / "supervisor.db"
    pid = process_orchestrator.create_process_run(
        task="Спарси площадку и собери Excel",
        acceptance="Нужен файл и короткий отчёт.",
        route=route or _parser_route(),
        supervisor_task_id="sup-1",
        process_id_value="proc-1",
        store_path=process_store,
    )
    process_orchestrator.update_process(pid, status=status, current_phase=status, store_path=process_store)
    return process_store, supervisor_store, pid


def test_worklog_marks_parser_return_to_bot1_as_auto_continue_candidate(tmp_path: Path) -> None:
    process_store, supervisor_store, pid = _create_process(tmp_path, status="return_to_bot1")
    process_orchestrator.add_assignment(
        pid,
        "bot1",
        "execution",
        "completed",
        {"result": "first pass"},
        store_path=process_store,
    )
    process_orchestrator.add_assignment(
        pid,
        "bot2",
        "quality_gate",
        "completed",
        {"verdict": {"status": "REQUEST_CHANGES", "summary": "Need export evidence"}},
        store_path=process_store,
    )
    process_orchestrator.add_process_event(
        pid,
        "process_next_action",
        {
            "action": process_log.RETURN_TO_BOT1_ACTION,
            "target_worker": "bot1",
            "source": "parsing_autonomy_policy",
            "required_fixes": ["Add Excel export evidence"],
        },
        store_path=process_store,
    )

    worklog = process_log.process_worklog(
        pid,
        process_store=process_store,
        supervisor_store=supervisor_store,
    )

    assert worklog["status"] == "return_to_bot1"
    assert "bot1:execution" in worklog["done"]
    assert "bot2:quality_gate" in worklog["done"]
    assert "bot1_revision" in worklog["not_done"]
    assert worklog["decision"]["state"] == "ready_to_continue"
    assert worklog["decision"]["auto_continue_allowed"] is True
    assert "process_orchestrator.py" in worklog["decision"]["resume_command"]


def test_worklog_never_autocontinues_when_human_decision_is_required(tmp_path: Path) -> None:
    process_store, supervisor_store, pid = _create_process(tmp_path, status="awaiting_human_decision")
    process_orchestrator.add_assignment(
        pid,
        "supervisor",
        "human_decision",
        "waiting",
        {"message": "Bot#2 asks for human decision"},
        store_path=process_store,
    )
    process_orchestrator.add_process_event(
        pid,
        "human_notification",
        {"delivery": {"telegram_delivered": True}},
        store_path=process_store,
    )

    worklog = process_log.process_worklog(
        pid,
        process_store=process_store,
        supervisor_store=supervisor_store,
    )

    assert worklog["waiting_on"] == "human"
    assert "human_decision" in worklog["not_done"]
    assert worklog["decision"]["state"] == "wait_human"
    assert worklog["decision"]["auto_continue_allowed"] is False
    assert worklog["decision"]["resume_command"] == ""


def test_worklog_detects_stale_running_without_duplicate_resume(tmp_path: Path) -> None:
    process_store, supervisor_store, pid = _create_process(tmp_path, status="running")
    old = "2026-06-16T00:00:00+00:00"
    now = process_log.parse_ts("2026-06-16T00:20:00+00:00")
    assert now is not None
    with process_orchestrator.connect(process_store) as con:
        con.execute("UPDATE process_runs SET updated_at=?, current_phase=? WHERE id=?", (old, "bot1", pid))
        con.commit()

    worklog = process_log.process_worklog(
        pid,
        process_store=process_store,
        supervisor_store=supervisor_store,
        now=now,
        idle_after_seconds=300,
    )

    assert worklog["stale"] is True
    assert worklog["idle_seconds"] == 1200
    assert worklog["decision"]["state"] == "watch_running"
    assert worklog["decision"]["auto_continue_allowed"] is False


def test_wakeback_sends_digest_and_continues_only_parser_return_loop(monkeypatch, tmp_path: Path) -> None:
    process_store, supervisor_store, pid = _create_process(tmp_path, status="return_to_bot1")
    process_orchestrator.add_process_event(
        pid,
        "process_next_action",
        {"action": process_log.RETURN_TO_BOT1_ACTION, "target_worker": "bot1", "source": "parsing_autonomy_policy"},
        store_path=process_store,
    )
    sent: list[str] = []
    continued: list[str] = []

    class FakeOrchestrator:
        DEFAULT_PROCESS_MAX_TOKENS = process_orchestrator.DEFAULT_PROCESS_MAX_TOKENS
        connect = staticmethod(process_orchestrator.connect)
        process_details = staticmethod(process_orchestrator.process_details)

        @staticmethod
        def continue_process(args):
            continued.append(args.process_id)
            return {"status": "continued", "mode": args.mode}

    monkeypatch.setattr(process_log, "_process_orchestrator", lambda: FakeOrchestrator)

    result = process_log.wakeback(
        process_store=process_store,
        supervisor_store=supervisor_store,
        send_telegram=True,
        telegram_sender=lambda text: sent.append(text) or {"telegram_delivered": True},
        auto_continue=True,
    )

    assert result["checked"] == 1
    assert result["selected"] == 1
    assert sent and "Not done:" in sent[0]
    assert continued == [pid]
    assert result["continuations"][0]["result"]["status"] == "continued"
