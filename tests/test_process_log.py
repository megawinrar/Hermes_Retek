from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import process_log  # noqa: E402


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
