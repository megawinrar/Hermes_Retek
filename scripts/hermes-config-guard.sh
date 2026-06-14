#!/bin/bash
set -euo pipefail
# Hermes Config Guard. Detection-only by default.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${HERMES_CONFIG_PATH:-/var/lib/docker/volumes/hermes-data/_data/config.yaml}"
STATE_DB="${HERMES_STATE_DB:-/var/lib/docker/volumes/hermes-data/_data/state.db}"
CORRECT_MODEL="${HERMES_MODEL:-deepseek-v4-flash}"
CORRECT_URL="${BOTHUB_BASE_URL:-https://openai.bothub.chat/v1}"
REPAIR="${HERMES_CONFIG_GUARD_REPAIR:-0}"
APPROVED="${HERMES_SUPERVISOR_APPROVED:-0}"

load_bothub_key() {
  if [ -n "${BOTHUB_API_KEY:-}" ]; then
    printf '%s' "$BOTHUB_API_KEY"
    return 0
  fi
  if [ -n "${BOTHUB_API_KEY_FILE:-}" ] && [ -r "$BOTHUB_API_KEY_FILE" ]; then
    tr -d '\r\n' < "$BOTHUB_API_KEY_FILE"
    return 0
  fi
  default_file="/var/lib/docker/volumes/hermes-data/_data/.secrets/bothub_api_key"
  if [ -r "$default_file" ]; then
    tr -d '\r\n' < "$default_file"
    return 0
  fi
  return 2
}

needs_repair=0
if [ -f "$CONFIG_PATH" ] && grep -Eiq 'yandex|hermes-yandex-proxy|sk-hermes-retek-proxy' "$CONFIG_PATH"; then
  echo "[$(date)] Drift detected in config.yaml"
  needs_repair=1
fi
if [ -f "$STATE_DB" ]; then
  YANDEX_COUNT=$(sqlite3 "$STATE_DB" "SELECT COUNT(*) FROM sessions WHERE model LIKE '%yandex%' OR billing_base_url LIKE '%yandex-proxy%';" 2>/dev/null || echo 0)
  if [ "$YANDEX_COUNT" -gt 0 ] 2>/dev/null; then
    echo "[$(date)] Drift detected in state.db"
    needs_repair=1
  fi
fi

if [ "$needs_repair" -eq 0 ]; then
  echo "[$(date)] Hermes config guard OK"
  exit 0
fi
if [ "$REPAIR" != "1" ] || [ "$APPROVED" != "1" ]; then
  echo "[$(date)] Repair blocked: set HERMES_CONFIG_GUARD_REPAIR=1 and HERMES_SUPERVISOR_APPROVED=1 after Supervisor/Bot#2 approval" >&2
  exit 2
fi

CORRECT_KEY="$(load_bothub_key)"
if [ -f "$CONFIG_PATH" ]; then
  sed -i "s|default: yandexgpt/latest|default: $CORRECT_MODEL|" "$CONFIG_PATH"
  sed -i "s|base_url: http://hermes-yandex-proxy:8000/v1|base_url: $CORRECT_URL|" "$CONFIG_PATH"
  sed -i -E "s|api_key: .*|api_key: $CORRECT_KEY|" "$CONFIG_PATH"
  echo "[$(date)] config.yaml repaired"
fi
if [ -f "$STATE_DB" ]; then
  sqlite3 "$STATE_DB" "UPDATE sessions SET model='$CORRECT_MODEL', billing_base_url='$CORRECT_URL', billing_provider='custom' WHERE model LIKE '%yandex%' OR billing_base_url LIKE '%yandex-proxy%';" 2>/dev/null || true
  echo "[$(date)] state.db repaired"
fi
restart_args=(--reason "config_guard_repair" --container "${HERMES_RESTART_CONTAINER:-hermes-agent}")
if [ -n "${HERMES_SUPERVISOR_TASK_ID:-}" ]; then
  restart_args+=(--task-id "$HERMES_SUPERVISOR_TASK_ID")
fi
if [ "${HERMES_RESTART_NOTIFY_TELEGRAM:-0}" = "1" ]; then
  restart_args+=(--notify-telegram)
fi
if [ "${HERMES_SAFE_RESTART_FORCE:-0}" = "1" ]; then
  restart_args+=(--force)
fi
"$SCRIPT_DIR/hermes_safe_restart.sh" "${restart_args[@]}"
echo "[$(date)] hermes-agent restart requested through safe restart guard"
