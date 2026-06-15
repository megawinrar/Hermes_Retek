from __future__ import annotations

import argparse
import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_JOB_ID = "89bb5d5a6b14"
DEFAULT_JOB_NAME = "api-limits-check"
DEFAULT_CONTAINER_BASE_URL = "http://hermes-yandex-proxy:8000"
LEGACY_BASE_URLS = (
    "http://127.0.0.1:8001",
    "http://localhost:8001",
)


def patch_api_limits_job(
    jobs_data: dict[str, Any],
    *,
    job_id: str = DEFAULT_JOB_ID,
    job_name: str = DEFAULT_JOB_NAME,
    container_base_url: str = DEFAULT_CONTAINER_BASE_URL,
) -> tuple[dict[str, Any], bool]:
    """Patch Hermes cron api-limits-check URLs for execution inside Docker."""

    updated = copy.deepcopy(jobs_data)
    jobs = updated.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("cron jobs JSON must contain a list field named 'jobs'")

    changed = False
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("id") != job_id and job.get("name") != job_name:
            continue

        prompt = job.get("prompt", "")
        if not isinstance(prompt, str):
            raise ValueError(f"cron job {job.get('id') or job.get('name')} prompt must be a string")

        patched_prompt = prompt
        for legacy_url in LEGACY_BASE_URLS:
            patched_prompt = patched_prompt.replace(legacy_url, container_base_url)

        if patched_prompt != prompt:
            job["prompt"] = patched_prompt
            changed = True

    if changed:
        updated["updated_at"] = datetime.now(timezone.utc).isoformat()
    return updated, changed


def patch_jobs_file(path: Path, *, backup: bool = True, container_base_url: str = DEFAULT_CONTAINER_BASE_URL) -> bool:
    data = json.loads(path.read_text())
    updated, changed = patch_api_limits_job(data, container_base_url=container_base_url)
    if not changed:
        return False

    if backup:
        backup_path = path.with_suffix(path.suffix + f".bak-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(path, backup_path)

    path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Patch Hermes api-limits-check cron job for in-container execution")
    parser.add_argument("jobs_json", type=Path, help="Path to /opt/data/cron/jobs.json")
    parser.add_argument("--base-url", default=DEFAULT_CONTAINER_BASE_URL)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)

    changed = patch_jobs_file(args.jobs_json, backup=not args.no_backup, container_base_url=args.base_url)
    print("changed" if changed else "already-patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
