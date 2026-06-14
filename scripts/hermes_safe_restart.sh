#!/usr/bin/env bash
set -euo pipefail

# Single audited entrypoint for Hermes runtime restarts.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${HERMES_PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CONTAINER="${HERMES_RESTART_CONTAINER:-hermes-agent}"
SUPERVISOR_STORE="${SUPERVISOR_STORE_PATH:-/var/lib/docker/volumes/hermes-data/_data/supervisor_store.db}"
PROCESS_STORE="${PROCESS_STORE_PATH:-/var/lib/docker/volumes/hermes-data/_data/process_orchestrator_store.db}"
AUDIT_LOG="${HERMES_RESTART_AUDIT_LOG:-/var/log/hermes-restarts.log}"
LOCK_DIR="${HERMES_RESTART_LOCK_DIR:-/tmp/hermes-safe-restart.lock}"
REASON="${HERMES_RESTART_REASON:-manual}"
TASK_ID="${HERMES_SUPERVISOR_TASK_ID:-}"
FORCE=0
DRY_RUN=0
NOTIFY_TELEGRAM="${HERMES_RESTART_NOTIFY_TELEGRAM:-0}"

usage() {
  cat <<'USAGE'
Usage: hermes_safe_restart.sh [options]

Options:
  --reason TEXT             Human-readable restart reason.
  --task-id ID              Supervisor task id that authorized this restart.
  --container NAME          Docker container to restart (default: hermes-agent).
  --supervisor-store PATH   Supervisor SQLite store path.
  --process-store PATH      Process orchestrator SQLite store path.
  --audit-log PATH          Restart audit log path.
  --lock-dir PATH           Lock directory path.
  --notify-telegram         Send pre/post Telegram notifications through scripts/devlog.py.
  --no-notify-telegram      Disable Telegram notifications.
  --force                   Allow restart even when active work is detected.
  --dry-run                 Run checks and audit without calling docker restart.
  -h, --help                Show this help.
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --reason)
      REASON="${2:?--reason requires a value}"
      shift 2
      ;;
    --task-id)
      TASK_ID="${2:?--task-id requires a value}"
      shift 2
      ;;
    --container)
      CONTAINER="${2:?--container requires a value}"
      shift 2
      ;;
    --supervisor-store)
      SUPERVISOR_STORE="${2:?--supervisor-store requires a value}"
      shift 2
      ;;
    --process-store)
      PROCESS_STORE="${2:?--process-store requires a value}"
      shift 2
      ;;
    --audit-log)
      AUDIT_LOG="${2:?--audit-log requires a value}"
      shift 2
      ;;
    --lock-dir)
      LOCK_DIR="${2:?--lock-dir requires a value}"
      shift 2
      ;;
    --notify-telegram)
      NOTIFY_TELEGRAM=1
      shift
      ;;
    --no-notify-telegram)
      NOTIFY_TELEGRAM=0
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 64
      ;;
  esac
done

mkdir -p "$(dirname "$AUDIT_LOG")"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "restart already in progress: $LOCK_DIR" >&2
  exit 75
fi
cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

json_audit() {
  local event="$1"
  local status="$2"
  local details="${3:-}"
  EVENT="$event" STATUS="$status" DETAILS="$details" CONTAINER="$CONTAINER" REASON="$REASON" TASK_ID="$TASK_ID" \
    FORCE="$FORCE" DRY_RUN="$DRY_RUN" AUDIT_LOG="$AUDIT_LOG" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone

payload = {
    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "event": os.environ["EVENT"],
    "status": os.environ["STATUS"],
    "container": os.environ["CONTAINER"],
    "reason": os.environ["REASON"],
    "task_id": os.environ["TASK_ID"],
    "forced": os.environ["FORCE"] == "1",
    "dry_run": os.environ["DRY_RUN"] == "1",
    "details": os.environ.get("DETAILS", ""),
}
with open(os.environ["AUDIT_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
PY
}

send_telegram() {
  local status="$1"
  local body="$2"
  if [ "$NOTIFY_TELEGRAM" != "1" ]; then
    return 0
  fi
  PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" STATUS="$status" BODY="$body" CONTAINER="$CONTAINER" REASON="$REASON" TASK_ID="$TASK_ID" \
    python3 - <<'PY' >/dev/null 2>&1 || true
import os
from devlog import send_telegram_message

text = "\n".join(
    [
        "[Hermes Restart Guard]",
        f"Status: {os.environ['STATUS']}",
        f"Container: {os.environ['CONTAINER']}",
        f"Reason: {os.environ['REASON']}",
        f"Task: {os.environ.get('TASK_ID') or '-'}",
        "",
        os.environ["BODY"],
    ]
)
send_telegram_message(text)
PY
}

sqlite_scalar() {
  local db="$1"
  local sql="$2"
  if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "sqlite3 is required to inspect active work in $db" >&2
    return 70
  fi
  sqlite3 "$db" "$sql"
}

sqlite_table_exists() {
  local db="$1"
  local table="$2"
  if [ ! -f "$db" ]; then
    return 1
  fi
  [ "$(sqlite_scalar "$db" "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='$table';")" = "1" ]
}

active_supervisor_details() {
  if ! sqlite_table_exists "$SUPERVISOR_STORE" "supervisor_tasks"; then
    printf ''
    return 0
  fi
  local sql="
    SELECT COALESCE(group_concat(id || ':' || status, ', '), '')
    FROM (
      SELECT id, status
      FROM supervisor_tasks
      WHERE status IN ('running', 'awaiting_human_decision', 'return_to_bot1')
      ORDER BY updated_at DESC
      LIMIT 5
    );
  "
  sqlite_scalar "$SUPERVISOR_STORE" "$sql"
}

active_process_details() {
  if ! sqlite_table_exists "$PROCESS_STORE" "process_runs"; then
    printf ''
    return 0
  fi
  local sql="
    SELECT COALESCE(group_concat(id || ':' || status, ', '), '')
    FROM (
      SELECT id, status
      FROM process_runs
      WHERE status IN ('running', 'awaiting_human_decision', 'return_to_bot1')
      ORDER BY updated_at DESC
      LIMIT 5
    );
  "
  sqlite_scalar "$PROCESS_STORE" "$sql"
}

supervisor_active="$(active_supervisor_details)"
process_active="$(active_process_details)"
active_details=""
if [ -n "$supervisor_active" ]; then
  active_details="supervisor=$supervisor_active"
fi
if [ -n "$process_active" ]; then
  if [ -n "$active_details" ]; then
    active_details="$active_details; "
  fi
  active_details="${active_details}process=$process_active"
fi

if [ -n "$active_details" ] && [ "$FORCE" != "1" ]; then
  json_audit "restart_blocked" "active_work" "$active_details" >/dev/null
  send_telegram "blocked" "Active work detected: $active_details"
  echo "restart blocked: active work detected: $active_details" >&2
  exit 3
fi

json_audit "restart_requested" "accepted" "${active_details:-no_active_work}" >/dev/null
send_telegram "starting" "Restart accepted. Active work: ${active_details:-none}"

if [ "$DRY_RUN" = "1" ]; then
  json_audit "restart_skipped" "dry_run" "${active_details:-no_active_work}"
  send_telegram "dry-run" "Docker restart was skipped by --dry-run."
  exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
  json_audit "restart_failed" "missing_docker" "docker command not found" >/dev/null
  send_telegram "failed" "docker command not found"
  echo "docker command not found" >&2
  exit 69
fi

docker restart "$CONTAINER"
json_audit "restart_completed" "ok" "${active_details:-no_active_work}"
send_telegram "completed" "Docker restart completed."
