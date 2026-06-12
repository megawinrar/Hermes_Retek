# Hermes Supervisor MVP v1

This package adds a CLI-only Supervisor layer over Hermes Retek. It does not
modify `hermes-core`, does not install cron, does not deploy, and does not push.

## Files

- `configs/supervisor_roles.yaml`
- `configs/supervisor_pipeline.yaml`
- `scripts/supervisor_common.py`
- `scripts/supervisor_task.py`
- `scripts/supervisor_run.py`
- `scripts/supervisor_status.py`
- `scripts/devlog.py`
- `tests/test_supervisor_policy.py`
- `tests/test_supervisor_store.py`

## Store

Default SQLite path on the server:

```bash
/var/lib/docker/volumes/hermes-data/_data/supervisor_store.db
```

Tables:

- `supervisor_tasks`
- `supervisor_events`
- `supervisor_role_runs`
- `supervisor_artifacts`
- `supervisor_bot2_links`
- `supervisor_human_escalations`

## Create Task

```bash
cd /opt/hermes-assistant
python3 scripts/supervisor_task.py create --tz "Implement the requested change and verify it."
```

The command creates:

- `task_id`
- acceptance contract
- risk level
- status `created`

## Run Pipeline

```bash
python3 scripts/supervisor_run.py <task_id>
```

The MVP pipeline records Developer and Tester role runs, calls:

```bash
scripts/bot2_gate.py review
```

and saves `bot2_session_id` in `supervisor_bot2_links`.

By default Bot#2 keeps Telegram DevLog/escalation delivery enabled. Use
`--no-telegram` only for local tests or dry-runs where the user must not be
notified.

## Status

```bash
python3 scripts/supervisor_status.py list
python3 scripts/supervisor_status.py show <task_id>
```

## Human Да/Нет Decision

If Bot#2 returns `REJECT` or `NEEDS_HUMAN`, Supervisor stores an escalation and
sets task status to:

```text
awaiting_human_decision
```

Decision semantics:

```text
Да / yes = agree with Bot#2, return Bot#1 to fixes.
Нет / no = reject Bot#2 objection, accept Bot#1 as-is.
```

Record the decision:

```bash
python3 scripts/supervisor_task.py decide <task_id> --choice yes --reason "Bot#2 risk is valid"
python3 scripts/supervisor_task.py decide <task_id> --choice no --reason "Risk accepted by user"
```

The Supervisor status becomes:

- `return_to_bot1` for `yes`
- `accepted_by_user_override` for `no`

The command also calls:

```bash
scripts/bot2_gate.py decide <bot2_session_id> --choice yes|no
```

unless `--skip-bot2-decide` is provided.

## Local Test Commands

From this package root:

```bash
python -m pytest tests
```

Manual local dry-run with a stub Bot#2 gate:

```bash
python scripts/supervisor_task.py --store /tmp/supervisor.db create --tz "Test Supervisor MVP"
python scripts/supervisor_run.py --store /tmp/supervisor.db --bot2-gate /path/to/stub_bot2_gate.py <task_id>
python scripts/supervisor_status.py --store /tmp/supervisor.db show <task_id>
```
