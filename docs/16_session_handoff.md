# Hermes Retek Session Handoff

Date: 2026-06-13

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
