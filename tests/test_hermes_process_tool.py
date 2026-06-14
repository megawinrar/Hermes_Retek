from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "custom" / "tools" / "hermes_process_tool.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("hermes_process_tool_under_test", TOOL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_run_command_defaults_to_live_process() -> None:
    tool = load_tool()
    cmd = tool.build_command({"action": "run", "task": "Deploy CRM fix", "acceptance": "tests pass"})

    assert cmd[:2] == [tool.DEFAULT_PYTHON, str(tool.DEFAULT_ORCHESTRATOR)]
    assert "--process-store" in cmd
    assert tool.DEFAULT_PROCESS_STORE in cmd
    assert "--supervisor-store" in cmd
    assert tool.DEFAULT_SUPERVISOR_STORE in cmd
    assert "--live-dual" in cmd
    assert "--live-route-audit" in cmd
    assert "--notify-telegram" in cmd
    assert "--notification-dry-run" not in cmd
    assert cmd[cmd.index("--bot1-model") + 1] == "auto"
    assert cmd[cmd.index("--bot2-model") + 1] == "auto"


def test_build_decide_command_validates_choice() -> None:
    tool = load_tool()
    cmd = tool.build_command(
        {
            "action": "decide",
            "process_id": "proc-123",
            "choice": "yes",
            "reason": "Bot2 is right",
        }
    )

    assert cmd[-6:] == ["decide", "proc-123", "--choice", "yes", "--reason", "Bot2 is right"]


def test_build_continue_command_defaults_to_auto_and_notifications() -> None:
    tool = load_tool()
    cmd = tool.build_command({"action": "continue", "process_id": "proc-123"})

    assert cmd[:2] == [tool.DEFAULT_PYTHON, str(tool.DEFAULT_ORCHESTRATOR)]
    assert cmd[cmd.index("continue") + 1] == "proc-123"
    assert cmd[cmd.index("--mode") + 1] == "auto"
    assert cmd[cmd.index("--bot1-model") + 1] == "auto"
    assert cmd[cmd.index("--bot2-model") + 1] == "auto"
    assert "--notify-telegram" in cmd
    assert "--notification-dry-run" not in cmd


def test_summary_extracts_process_runtime_fields() -> None:
    tool = load_tool()
    payload = {
        "process_id": "proc-1",
        "supervisor_task_id": "sup-1",
        "status": "awaiting_human_decision",
        "route": {"task_level": "L4", "task_type": "deploy", "risk_level": "high"},
        "bot2_session_id": "dual-1",
        "bot2_verdict": {
            "status": "REQUEST_CHANGES",
            "summary": "Need tests",
            "risks": ["missing tests"],
            "review_cycles": [{"round": 1}, {"round": 2}],
            "repair_attempted": True,
            "repair_status": "repaired",
        },
        "performance": {
            "duration_ms": 321,
            "route_audit": {"status": "CONFIRM"},
            "live_review": {"cycle_count": 1, "llm_call_count": 2, "latency_ms": 123},
        },
    }

    summary = tool.summarize_payload("run", payload)

    assert summary["process_id"] == "proc-1"
    assert summary["status"] == "awaiting_human_decision"
    assert summary["task_level"] == "L4"
    assert summary["bot2"]["status"] == "REQUEST_CHANGES"
    assert summary["bot2"]["review_cycle_count"] == 2
    assert summary["bot2"]["repair_attempted"] is True
    assert summary["performance"]["duration_ms"] == 321
    assert summary["performance"]["live_review"]["llm_call_count"] == 2
    assert summary["performance"]["live_review"]["latency_ms"] == 123


def test_summary_marks_human_decision_required_from_run_payload() -> None:
    tool = load_tool()
    payload = {
        "process_id": "proc-3",
        "status": "awaiting_human_decision",
        "route": {"task_level": "L4", "task_type": "deploy", "risk_level": "high"},
        "human_notification": {
            "decision_semantics": {
                "yes": "Return Bot#1 to fixes.",
                "no": "Accept Bot#1 override.",
            }
        },
    }

    summary = tool.summarize_payload("run", payload)

    assert summary["human_decision"] == {
        "required": True,
        "status": "awaiting_decision",
        "choice": None,
        "yes_meaning": "Return Bot#1 to fixes.",
        "no_meaning": "Accept Bot#1 override.",
    }


def test_summary_does_not_treat_live_bot1_run_id_as_bot2_session() -> None:
    tool = load_tool()
    payload = {
        "process_id": "proc-4",
        "status": "approved",
        "route": {
            "task_level": "L2",
            "task_type": "simple_text_task",
            "risk_level": "low",
            "review_required": False,
            "human_gate_required": False,
        },
        "bot2_session_id": "dual-live-bot1-run-id",
        "bot2_verdict": {},
    }

    summary = tool.summarize_payload("run", payload)

    assert summary["bot2"]["required"] is False
    assert summary["bot2"]["session_id"] == ""


def test_execute_returns_compact_json_from_orchestrator(monkeypatch) -> None:
    tool = load_tool()
    monkeypatch.setenv("HERMES_PROCESS_EXECUTION_MODE", "subprocess")

    def fake_run(cmd, *, timeout):
        assert "--live-dual" not in cmd
        assert timeout == 55
        stdout = json.dumps(
            {
                "process_id": "proc-2",
                "status": "approved",
                "route": {"task_level": "L1", "task_type": "general", "risk_level": "low"},
            }
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(tool, "run_orchestrator", fake_run)

    result = json.loads(tool.execute(action="run", task="short task", live_dual=False, timeout=40))

    assert result["ok"] is True
    assert result["process_id"] == "proc-2"
    assert result["status"] == "approved"
    assert result["adapter"]["execution_mode"] == "subprocess"
    assert "raw" not in result


def test_execute_runs_orchestrator_in_process() -> None:
    tool = load_tool()

    with tempfile.TemporaryDirectory(prefix="hermes-process-tool-") as tmp:
        tool.DEFAULT_PROJECT_DIR = ROOT
        tool.DEFAULT_ORCHESTRATOR = ROOT / "scripts" / "process_orchestrator.py"
        tool.DEFAULT_PROCESS_STORE = str(Path(tmp) / "process.db")
        tool.DEFAULT_SUPERVISOR_STORE = str(Path(tmp) / "supervisor.db")
        tool._ORCHESTRATOR_CACHE = None

        run_result = json.loads(
            tool.execute(
                action="run",
                task="status",
                acceptance="ok",
                live_dual=False,
                live_route_audit=False,
                notify_telegram=False,
                notification_dry_run=True,
                timeout=30,
            )
        )
        show_result = json.loads(
            tool.execute(
                action="show",
                process_id=run_result["process_id"],
                timeout=30,
            )
        )

    assert run_result["ok"] is True
    assert run_result["adapter"]["execution_mode"] == "in_process"
    assert run_result["status"] == "approved"
    assert show_result["ok"] is True
    assert show_result["adapter"]["execution_mode"] == "in_process"
    assert show_result["process_id"] == run_result["process_id"]


def test_execute_reports_nonzero_exit(monkeypatch) -> None:
    tool = load_tool()
    monkeypatch.setenv("HERMES_PROCESS_EXECUTION_MODE", "subprocess")

    def fake_run(cmd, *, timeout):
        return subprocess.CompletedProcess(cmd, 2, stdout="", stderr="bad route")

    monkeypatch.setattr(tool, "run_orchestrator", fake_run)

    result = json.loads(tool.execute(action="route", task="x"))

    assert result["ok"] is False
    assert result["action"] == "route"
    assert result["error"] == "bad route"
    assert result["exit_code"] == 2
    assert result["adapter"]["execution_mode"] == "subprocess"


def test_subprocess_env_moves_runtime_state_to_opt_data(monkeypatch) -> None:
    tool = load_tool()
    monkeypatch.delenv("DUAL_BOT_REPORT_DIR", raising=False)

    env = tool._subprocess_env()

    assert env["PROCESS_STORE_PATH"] == "/opt/data/process_orchestrator_store.db"
    assert env["SUPERVISOR_STORE_PATH"] == "/opt/data/supervisor_store.db"
    assert env["DUAL_BOT_LAB_STORE"] == "/opt/data/dual_bot_lab_store.db"
    assert env["DUAL_BOT_REPORT_DIR"] == "/opt/data/reports"
