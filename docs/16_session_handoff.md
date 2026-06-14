# Hermes Retek Session Handoff

Date: 2026-06-14

## Current Continuation Point

The current active branch is `ops-safe-restart-speed`.

Latest pushed commits at handoff:

- `bbfea2e feat: add hermes timing report`
- `daf1bff feat: log hermes agent fanout timings`

The live server has the timing report script physically deployed to
`/opt/hermes-assistant/scripts/hermes_timing_report.py`. The production tree on
the server is still on its own `custom` branch, so do not blindly switch it or
run destructive sync commands.

One-time timing report is scheduled through `yc-user` crontab for
2026-06-15 06:05 UTC, which is 2026-06-15 10:05 Europe/Samara. It sends a
Telegram report and writes runtime output to:

```text
/opt/data/reports/hermes_timing_report_20260615.log
```

The timing report now reads real runtime logs:

- `/opt/data/logs/gateway.log`
- `/opt/data/logs/agent.log`

It reports:

- Telegram turn timing: inbound, first streaming flush, final response;
- LLM/BotHub stream latency;
- tool latency and tool errors;
- agent fanout: sessions, `delegate_task`, background review turns, noisy
  sessions, agent `api_used`, history size;
- gateway SIGTERM/start events, Telegram reconnects, session compression;
- Process/Supervisor SQLite status counts.

The latest real dry-run showed Hermes already uses agent fanout:

- `agent sessions seen: 31`;
- `delegate_task calls: 11`;
- `background review turns: 32`;
- slowest `delegate_task`: about `393.6s`.

That means parallelization is realistic, but it must be bounded with max
parallel agents, per-agent timeout/budget, isolated workspaces, single-writer DB
rules, and BotHub rate/budget guards.

## Start Command For Next Window

Paste this into a fresh Codex window:

```text
Продолжи Hermes Retek с места остановки. Рабочая ветка: ops-safe-restart-speed. Сначала прочитай docs/16_session_handoff.md, затем проверь завтрашний Telegram timing report и /opt/data/reports/hermes_timing_report_20260615.log. После этого спроектируй bounded parallel agent orchestration: max parallel agents, per-agent timeout/budget, isolated workspace, single-writer SQLite/state, BotHub rate limits, и добавь тесты. Не переключай production /opt/hermes-assistant с ветки custom без явного разрешения.
```

Useful manual server check:

```bash
docker exec hermes-agent sh -lc 'cd /opt/hermes-assistant && python3 scripts/hermes_timing_report.py --hours 24'
```

Useful focused test:

```bash
docker exec -e UV_CACHE_DIR=/opt/data/.cache/uv -e PYTHONDONTWRITEBYTECODE=1 hermes-agent sh -lc 'cd /opt/hermes-assistant && uv run --with pytest==9.0.2 --with pytest-timeout==2.4.0 python -m pytest tests/test_hermes_timing_report.py -q -p no:cacheprovider'
```

Latest verification before this handoff:

- focused timing report tests: `8 passed`;
- full server suite: `137 passed`;
- current-file secret audit for changed files: `0 findings`.

## Current Runtime Correction

The live Hermes runtime has been re-checked on 2026-06-13.

Use `docs/17_hermes_runtime_integration.md` as the current integration map.
Important correction: the live application tree is `/opt/hermes-assistant`, and
the Telegram agent runs in the `hermes-agent` Docker container. The Retek
`scripts/` in this repository are host-side Supervisor/Bot#2/process tooling;
they are not imported by `/opt/hermes` inside the running container.

Avoid blind `git pull` or full sync on `/opt/hermes-assistant`; the server tree
contains local changes, untracked files, Docker mounts, and mixed ownership.

## How To Continue In A New Codex Session

Open a new Codex session and provide this file. Suggested first message:

```text
Continue Hermes Retek from docs/16_session_handoff.md. Server paths are /opt/Hermes_Retek and /opt/hermes-assistant. First read docs/14_stage2_done_report.md and docs/15_remaining_work_plan.md, then continue with P1 Human Notification / Telegram DevLog.
```

## Project Context

Repository: `megawinrar/Hermes_Retek`.

Server paths:

- repo checkout: `/opt/Hermes_Retek`
- live app copy: `/opt/hermes-assistant`
- reports: `/opt/hermes-assistant/reports`
- default supervisor store: `/var/lib/docker/volumes/hermes-data/_data/supervisor_store.db`
- default process store: `/var/lib/docker/volumes/hermes-data/_data/process_orchestrator_store.db`

SSH context:

- user: `yc-user`
- host: `89.169.142.160`
- working Windows key copy used in this session: `C:\Temp\yandex_key_clean`
- Windows OpenSSH is old; use `-E <logfile>` or commands may hang.

Reliable SSH shape:

```powershell
$key = 'C:\Temp\yandex_key_clean'
$kh = '<workspace>\work\known_hosts'
$log = '<workspace>\work\ssh_run.log'
& 'C:\Windows\System32\OpenSSH\ssh.exe' -E $log -T -i $key -o LogLevel=ERROR -o IdentitiesOnly=yes -o BatchMode=yes -o PreferredAuthentications=publickey -o PasswordAuthentication=no -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$kh yc-user@89.169.142.160 "cd /opt/Hermes_Retek && git status --short"
```

## Current Git State

Latest pushed Stage 2 commits:

- `020a3be Add Stage 2 safety gates and secret scan`
- `2ffcbc0 Restore CLI script executable bits`

This handoff update should be a docs-only commit after those.

## Already Done

Read full detail in `docs/14_stage2_done_report.md`.

Short version:

- hardcoded API secrets removed from current tracked shell scripts;
- secret scan test added;
- Router separates GitHub read-only lookup from GitHub write/deploy actions;
- L0/L1 no longer start Bot#2 by default;
- high-risk push/merge/deploy goes to human gate;
- Bot#2 canonical verdict enum added;
- invalid Bot#2 JSON fails closed;
- live copy `/opt/hermes-assistant` synced;
- tests passed: `26 passed`.

## Remaining Work

Read full detail in `docs/15_remaining_work_plan.md`.

Priority order:

1. P0 rotate exposed Bothub/API key in the external service.
2. P1 Human Notification / Telegram DevLog.
3. P1 Bot#2 retry/repair for invalid JSON.
4. P1 DevOps/tool gateway.
5. P1 process state machine hardening.
6. P2 skills index/lazy loading.
7. P2 observability dashboard.
8. Stage 2 battle suite with real tasks.

## Recommended Next Task

Start with `P1 Human Notification / Telegram DevLog`.

Goal: when a process enters `awaiting_human_decision`, the user receives a real-time message containing:

- task;
- Bot#1 version;
- Bot#2 version;
- risk;
- recommendation;
- clear Yes/No semantics.

Acceptance:

- dry-run payload can be tested without Telegram;
- integration test proves notification payload shape;
- live process records notification event;
- no secrets appear in notification/logs.

## Important Risk

The old API key was removed from current files, but it existed in git history. Treat it as compromised. Rotate it outside the repo.
