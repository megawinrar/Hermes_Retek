# Hermes Retek Stage 2 Done Report

Date: 2026-06-13

## Short Summary

Stage 2 implemented the first safety kernel for Hermes Retek.

Completed:

- removed hardcoded API secrets from current tracked shell scripts;
- added secret scan test;
- made dangerous automation fail closed by default;
- fixed Router classification for GitHub lookup vs write/deploy;
- made Process Orchestrator obey route policy;
- stopped L0/L1 from launching Bot#2 by default;
- made high-risk push/merge/deploy require human gate;
- added canonical Bot#2 verdict statuses;
- made invalid Bot#2 output fail closed;
- synced live app copy;
- pushed code to GitHub.

## GitHub Commits

- `020a3be Add Stage 2 safety gates and secret scan`
- `2ffcbc0 Restore CLI script executable bits`

## Security / P0 Done

Changed files:

- `scripts/check_api_limits.sh`
- `scripts/hermes-config-guard.sh`
- `scripts/auto-push.sh`
- `tests/test_secret_scan.py`

Details:

- `check_api_limits.sh` no longer contains a hardcoded Bothub key.
- It reads the key from `BOTHUB_API_KEY`, `BOTHUB_API_KEY_FILE`, or server secret file.
- `hermes-config-guard.sh` is detection-only by default.
- Config repair requires explicit approval flags:
  - `HERMES_CONFIG_GUARD_REPAIR=1`
  - `HERMES_SUPERVISOR_APPROVED=1`
- `auto-push.sh` is fail-closed by default.
- Autosync requires:
  - `HERMES_ALLOW_AUTOPUSH=1`
  - `HERMES_SUPERVISOR_APPROVED=1`
- Added secret scan over `scripts/` and `configs/`.

Remaining security note:

- The old key was present in git history. It must be rotated externally.

## Router Done

Changed file:

- `scripts/task_router.py`

Behavior now covered:

- `status` -> `L0 command_or_status`.
- Short rewrite/sanity tasks -> `L1 simple_text_task`.
- GitHub read-only lookup -> `L2 github_lookup`.
- GitHub push/merge/deploy/release/main -> `L4 git_write_or_deploy` with high risk.
- Database migration planning -> `L3 database_migration_plan`.
- Database migration changes/apply/deploy -> `L4 database_migration_change`.
- `human_gate_required` is present on all route outputs.

## Process Orchestrator Done

Changed file:

- `scripts/process_orchestrator.py`

Behavior now covered:

- L0 does not run Bot#1 or Bot#2.
- L1 runs Bot#1 only, unless review/risk policy requires Bot#2.
- Bot#2 starts based on `review_required`, `human_gate_required`, or L3/L4 route.
- Tester starts only when route policy needs evidence/review.
- Human gate blocks high-risk writes even if Bot#2 dry verdict says `APPROVE`.
- Route-policy verdict is generated when human gate is required.
- Live Bot#1-only path exists for no-Bot#2 routes.

## Bot#2 Contract Done

Changed files:

- `scripts/supervisor_common.py`
- `scripts/dual_bot_lab.py`

Added canonical statuses:

- `APPROVE`
- `APPROVE_WITH_EVIDENCE`
- `REQUEST_CHANGES`
- `REJECT`
- `NEEDS_HUMAN`
- `INSUFFICIENT_EVIDENCE`
- `MISSING_TESTS_FOR_CODE_CHANGE`
- `FAKE_IMPLEMENTATION_DETECTED`
- `TEST_THEATER_DETECTED`
- `RUBBER_STAMP_RISK`
- `BLOCKED_BY_POLICY`
- `LOOP_DETECTED`
- `INVALID_BOT2_OUTPUT`

Added `approved_action` dimension:

- `execute`
- `refuse`
- `no_op`
- `needs_human`

Important behavior:

- `approved_action=refuse` is not DevOps execution approval.
- Invalid or unknown Bot#2 status maps to fail-closed behavior.
- JSON parsing is stricter and no longer accepts random embedded JSON fragments from logs.

## Tests Done

Changed/added tests:

- `tests/test_task_router.py`
- `tests/test_process_orchestrator.py`
- `tests/test_secret_scan.py`

Full test command:

```bash
python3 -m pytest tests
```

Result:

```text
26 passed
```

Manual checks:

- `status` -> approved, L0, no Bot#2.
- `rewrite short hello` -> approved, L1, no Bot#2.
- `Look up GitHub issue #12 and summarize status` -> `L2 github_lookup`.
- `merge PR #12, push to main, and deploy production` -> `awaiting_human_decision`.

## Live Copy Done

Synced to `/opt/hermes-assistant/scripts/`:

- `task_router.py`
- `process_orchestrator.py`
- `supervisor_common.py`
- `dual_bot_lab.py`
- `check_api_limits.sh`
- `hermes-config-guard.sh`
- `auto-push.sh`

Smoke check:

- live route `status` -> `L0`.
- live L1 run -> `approved`, no Bot#2 session.

## Server Cleanup Done

Safe cleanup only:

- apt cache;
- old tmp files;
- journal vacuum.

After cleanup:

```text
/dev/vda1: 19G total, 15G used, 4.0G available, 79%
```

Not touched:

- Docker images;
- Docker volumes;
- Hermes backups;
- live data.
