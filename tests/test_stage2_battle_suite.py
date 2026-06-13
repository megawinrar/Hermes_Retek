from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def test_stage2_battle_suite_passes_and_saves_report(tmp_path: Path) -> None:
    json_out = tmp_path / "battle.json"
    report_dir = tmp_path / "reports"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "stage2_battle_suite.py"),
            "--report-dir",
            str(report_dir),
            "--json-out",
            str(json_out),
        ],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["case_count"] == 10
    assert payload["failed_count"] == 0
    assert json_out.exists()
    report = Path(payload["report_path"])
    assert report.exists()
    text = report.read_text(encoding="utf-8")
    assert "Stage 2 Battle Suite" in text
    assert "DevOps gate blocks restart before approval" in text
