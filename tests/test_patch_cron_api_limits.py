from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import patch_cron_api_limits  # noqa: E402


def _jobs_data() -> dict:
    return {
        "jobs": [
            {
                "id": "89bb5d5a6b14",
                "name": "api-limits-check",
                "prompt": (
                    "curl -s http://127.0.0.1:8001/v1/budget\n"
                    "curl -s http://localhost:8001/v1/usage/today"
                ),
                "schedule": {"kind": "cron", "expr": "0 */6 * * *"},
                "enabled": True,
            },
            {
                "id": "other",
                "name": "other-job",
                "prompt": "curl -s http://127.0.0.1:8001/keep-local",
            },
        ],
        "updated_at": "2026-06-15T00:00:00+00:00",
    }


def test_patch_api_limits_job_uses_container_service_url_and_preserves_other_jobs() -> None:
    updated, changed = patch_cron_api_limits.patch_api_limits_job(_jobs_data())

    assert changed is True
    api_job = updated["jobs"][0]
    assert "http://hermes-yandex-proxy:8000/v1/budget" in api_job["prompt"]
    assert "http://hermes-yandex-proxy:8000/v1/usage/today" in api_job["prompt"]
    assert "127.0.0.1:8001" not in api_job["prompt"]
    assert "localhost:8001" not in api_job["prompt"]
    assert api_job["schedule"]["expr"] == "0 */6 * * *"
    assert api_job["enabled"] is True
    assert updated["jobs"][1]["prompt"] == "curl -s http://127.0.0.1:8001/keep-local"
    assert updated["updated_at"] != "2026-06-15T00:00:00+00:00"


def test_patch_api_limits_job_is_idempotent() -> None:
    first, changed = patch_cron_api_limits.patch_api_limits_job(_jobs_data())
    second, changed_again = patch_cron_api_limits.patch_api_limits_job(first)

    assert changed is True
    assert changed_again is False
    assert second == first


def test_patch_api_limits_job_can_switch_to_no_agent_script_mode() -> None:
    updated, changed = patch_cron_api_limits.patch_api_limits_job(_jobs_data(), no_agent=True)

    assert changed is True
    api_job = updated["jobs"][0]
    assert api_job["no_agent"] is True
    assert api_job["script"] == "hermes_budget_report.py"
    assert api_job["prompt"] == "Deterministic Hermes LLM budget report. Script mode: no LLM agent required."
    assert api_job["schedule"]["expr"] == "0 */6 * * *"
    assert updated["jobs"][1].get("script") is None

    second, changed_again = patch_cron_api_limits.patch_api_limits_job(updated, no_agent=True)
    assert changed_again is False
    assert second == updated


def test_patch_jobs_file_writes_backup_and_json(tmp_path: Path) -> None:
    jobs_path = tmp_path / "jobs.json"
    jobs_path.write_text(json.dumps(_jobs_data(), ensure_ascii=False))

    changed = patch_cron_api_limits.patch_jobs_file(jobs_path, no_agent=True)

    assert changed is True
    assert list(tmp_path.glob("jobs.json.bak-*"))
    saved = json.loads(jobs_path.read_text())
    assert saved["jobs"][0]["script"] == "hermes_budget_report.py"
    assert saved["jobs"][0]["no_agent"] is True
