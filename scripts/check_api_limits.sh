#!/bin/bash
set -euo pipefail

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
  echo "BOTHUB_API_KEY or BOTHUB_API_KEY_FILE is required" >&2
  return 2
}

API_KEY="$(load_bothub_key)"
TMP_RESPONSE="$(mktemp)"
trap 'rm -f "$TMP_RESPONSE"' EXIT

HTTP_CODE=$(curl -sS -o "$TMP_RESPONSE" -w '%{http_code}' -X POST "${BOTHUB_BASE_URL:-https://openai.bothub.chat/v1}/chat/completions" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-flash","messages":[{"role":"user","content":"ping"}],"max_tokens":3}' 2>/dev/null || true)

if [ "$HTTP_CODE" = "200" ] && grep -q '"id"' "$TMP_RESPONSE"; then
  echo "Hermes API OK | Bothub DeepSeek V4 Flash | $(date '+%H:%M %Z')"
else
  echo "Hermes API FAIL | http_status=${HTTP_CODE:-curl_error} | $(date '+%H:%M %Z')"
  exit 1
fi
