from __future__ import annotations

import py_compile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def test_process_worker_config_exists() -> None:
    config = ROOT / "configs" / "process_workers.yaml"
    text = config.read_text(encoding="utf-8")
    assert "supervisor_is_only_state_owner: true" in text
    assert "bot1_bot2_direct_chat_forbidden: true" in text
    assert "deepseek-v4-flash" in text
    assert "gpt-5.3-codex" in text


def test_scripts_compile() -> None:
    scripts = [
        SCRIPTS / "task_router.py",
        SCRIPTS / "process_orchestrator.py",
        SCRIPTS / "bot2_gate.py",
        SCRIPTS / "tool_gateway.py",
        SCRIPTS / "supervisor_common.py",
        SCRIPTS / "dual_bot_lab.py",
        SCRIPTS / "dual_bot_suite.py",
    ]
    with tempfile.TemporaryDirectory() as tmp:
        for script in scripts:
            py_compile.compile(str(script), cfile=str(Path(tmp) / f"{script.stem}.pyc"), doraise=True)
