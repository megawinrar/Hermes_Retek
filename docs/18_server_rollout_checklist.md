# Hermes Retek Server Rollout Checklist

Date: 2026-06-13

Scope: controlled rollout for the Bot#2 gate, Supervisor state machine,
DevOps tool gateway, resource locks, and process observability dashboard.

## Rollout Rule

Do not run blind `git pull`, `git reset`, or broad directory sync on the live
server.

The server may contain operational edits outside the repository branch. Every
runtime change must be backed up, copied intentionally, smoke-tested, and
rollback-ready.

## Preflight

1. Confirm PR branch and commit:

```bash
git status --short --branch
git log --oneline -5
```

2. Run local tests:

```bash
.venv/bin/python -m pytest -q
```

3. Inspect server state read-only:

```bash
HERMES_SERVER=yc-user@SERVER_IP
ssh "$HERMES_SERVER" 'cd /opt/hermes-assistant && git status --short --branch && docker ps --format "{{.Names}}\t{{.Status}}"'
```

4. Record active containers and mounted files:

```bash
ssh "$HERMES_SERVER" 'docker inspect hermes-agent --format "{{json .Mounts}}"'
```

## Backup

Create a timestamped backup before replacing any file:

```bash
TS=$(date -u +%Y%m%d-%H%M%S)
sudo cp -a /opt/hermes-assistant/scripts/bot2_gate.py /tmp/bot2_gate.py.before_$TS.bak 2>/dev/null || true
sudo cp -a /opt/hermes-assistant/scripts/tool_gateway.py /tmp/tool_gateway.py.before_$TS.bak 2>/dev/null || true
sudo cp -a /opt/hermes-assistant/scripts/process_orchestrator.py /tmp/process_orchestrator.py.before_$TS.bak 2>/dev/null || true
sudo cp -a /opt/hermes-assistant/scripts/supervisor_common.py /tmp/supervisor_common.py.before_$TS.bak 2>/dev/null || true
```

If SQLite stores already exist, copy them before first schema-opening command:

```bash
sudo cp -a /var/lib/docker/volumes/hermes-data/_data/supervisor_store.db /tmp/supervisor_store.db.before_$TS.bak 2>/dev/null || true
sudo cp -a /var/lib/docker/volumes/hermes-data/_data/process_orchestrator_store.db /tmp/process_orchestrator_store.db.before_$TS.bak 2>/dev/null || true
```

## Files To Roll Out

Copy only reviewed files from the PR branch:

- `scripts/bot2_gate.py`
- `scripts/tool_gateway.py`
- `scripts/supervisor_common.py`
- `scripts/process_orchestrator.py`
- `scripts/human_notification.py`
- `scripts/dual_bot_lab.py`
- `configs/runtime_integration.yaml`
- `AGENTS.md` if the reviewed branch differs from the live copy

## Smoke Tests

Run compile checks first:

```bash
cd /opt/hermes-assistant
python3 -m py_compile scripts/bot2_gate.py scripts/tool_gateway.py scripts/supervisor_common.py scripts/process_orchestrator.py
```

Check Bot#2 fail-closed path without Telegram:

```bash
python3 scripts/bot2_gate.py review \
  --task "Smoke: check strict Bot2 gate" \
  --acceptance "Return a machine-readable verdict or fail closed" \
  --bot1-result "No production files changed. Smoke only." \
  --evidence "py_compile passed" \
  --no-telegram
```

Check tool gateway blocks dangerous DevOps without approval:

```bash
python3 scripts/tool_gateway.py check -- git push origin main
python3 scripts/tool_gateway.py check -- docker restart hermes-agent
```

Expected: non-zero exit with `allowed=false`.

Check process dashboard and JSONL events:

```bash
RUN_JSON=$(python3 scripts/process_orchestrator.py run \
  --task "merge PR #12, push to main, and deploy production" \
  --bot2-status APPROVE \
  --notification-dry-run)
PID=$(printf "%s" "$RUN_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["process_id"])')
python3 scripts/process_orchestrator.py show "$PID"
python3 scripts/process_orchestrator.py events "$PID"
```

Expected:

- process status is `awaiting_human_decision`;
- `summary.waiting_on` is `human`;
- `summary.notification.mode` is `dry_run`;
- JSONL event output is redacted.

## Container Restart Policy

Restart `hermes-agent` only when a mounted file needs container visibility.

Before restart:

```bash
docker ps --filter name=hermes-agent
```

Restart through the gateway once approval exists. Until gateway is adopted as
the operational wrapper, manual restart remains a human-approved rollout step:

```bash
docker restart hermes-agent
```

After restart:

```bash
docker exec hermes-agent /bin/sh -lc 'grep -n "RUNTIME BOUNDARY" /opt/hermes-assistant/AGENTS.md || true'
docker ps --filter name=hermes-agent
```

## Rollback

Restore backed-up files and rerun compile/smoke checks:

```bash
sudo cp -a /tmp/bot2_gate.py.before_$TS.bak /opt/hermes-assistant/scripts/bot2_gate.py
sudo cp -a /tmp/tool_gateway.py.before_$TS.bak /opt/hermes-assistant/scripts/tool_gateway.py
sudo cp -a /tmp/process_orchestrator.py.before_$TS.bak /opt/hermes-assistant/scripts/process_orchestrator.py
sudo cp -a /tmp/supervisor_common.py.before_$TS.bak /opt/hermes-assistant/scripts/supervisor_common.py
```

If a schema smoke test created bad state, restore SQLite backups before
restarting the operational flow.

## Done Criteria

- Local tests pass.
- Server compile checks pass.
- Bot#2 gate returns strict JSON or fail-closed JSON.
- Tool gateway blocks dangerous commands without linked approval.
- Dashboard `show` and `events` work on a smoke process.
- No secrets appear in stdout, reports, process events, or notification payloads.
- Rollback files are present until the next successful release window.
