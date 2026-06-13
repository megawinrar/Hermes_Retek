from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_real_task_suite_passes_and_redacts(tmp_path: Path) -> None:
    json_out = tmp_path / "real_task_suite.json"
    report_dir = tmp_path / "reports"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "real_task_suite.py"),
            "--report-dir",
            str(report_dir),
            "--json-out",
            str(json_out),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(json_out.read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert payload["case_count"] == 5
    assert payload["failed_count"] == 0
    assert "github_pat_" not in result.stdout
    assert "github_pat_" not in json_out.read_text(encoding="utf-8")
    report_path = Path(payload["report_path"])
    assert report_path.exists()
    assert "github_pat_" not in report_path.read_text(encoding="utf-8")
