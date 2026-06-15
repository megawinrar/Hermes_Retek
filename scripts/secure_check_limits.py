#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import shlex
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_SCRIPT_PATH = Path("/opt/data/cron/check_limits.sh")
DEFAULT_ENV_PATH = Path("/opt/data/cron/.check_limits.env")

SECURE_SCRIPT = """#!/bin/bash
set -euo pipefail

ENV_FILE="${CHECK_LIMITS_ENV_FILE:-/opt/data/cron/.check_limits.env}"
if [ -r "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

BASE_URL="${BOTHUB_BASE_URL:-https://openai.bothub.chat/v1}"
MODEL="${BOTHUB_MODEL:-deepseek-v4-flash}"
API_KEY="${BOTHUB_API_KEY:-}"

if [ -z "$API_KEY" ]; then
  echo "Hermes API FAIL | BOTHUB_API_KEY missing | $(date '+%Y-%m-%d %H:%M %Z')"
  exit 2
fi

TMP_RESPONSE="$(mktemp)"
cleanup() {
  rm -f "$TMP_RESPONSE"
}
trap cleanup EXIT

HTTP_CODE=$(curl -sS --max-time 20 -o "$TMP_RESPONSE" -w '%{http_code}' \\
  -X POST "$BASE_URL/chat/completions" \\
  -H "Authorization: Bearer ${API_KEY}" \\
  -H "Content-Type: application/json" \\
  -d "{\\"model\\":\\"${MODEL}\\",\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"Say OK\\"}],\\"max_tokens\\":5}" 2>/dev/null || true)

if [ "$HTTP_CODE" = "200" ] && grep -q '"id"' "$TMP_RESPONSE"; then
  MESSAGE="Hermes API OK | BothHub ${MODEL} | $(date '+%Y-%m-%d %H:%M %Z')"
  EXIT_CODE=0
else
  MESSAGE="Hermes API FAIL | http_status=${HTTP_CODE:-curl_error} | $(date '+%Y-%m-%d %H:%M %Z')"
  EXIT_CODE=1
fi

if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
  curl -sS --max-time 15 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \\
    -d "chat_id=${TELEGRAM_CHAT_ID}" \\
    --data-urlencode "text=${MESSAGE}" >/dev/null || true
fi

echo "$MESSAGE"
exit "$EXIT_CODE"
"""


def _extract_quoted_assignment(text: str, key: str) -> str:
    match = re.search(rf'^{re.escape(key)}="([^"]*)"', text, re.MULTILINE)
    return match.group(1) if match else ""


def secure_check_limits(
    script_path: Path = DEFAULT_SCRIPT_PATH,
    env_path: Path = DEFAULT_ENV_PATH,
    *,
    backup: bool = True,
) -> tuple[bool, Path | None]:
    original = script_path.read_text()
    backup_path: Path | None = None

    values = {
        "TELEGRAM_BOT_TOKEN": _extract_quoted_assignment(original, "TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID": _extract_quoted_assignment(original, "TELEGRAM_CHAT_ID"),
        "BOTHUB_API_KEY": _extract_quoted_assignment(original, "API_KEY")
        or _extract_quoted_assignment(original, "BOTHUB_API_KEY"),
    }
    env_lines = [f"{key}={shlex.quote(value)}" for key, value in values.items() if value]
    if env_lines and not env_path.exists():
        env_path.parent.mkdir(parents=True, exist_ok=True)
        env_path.write_text("\n".join(env_lines) + "\n")
        env_path.chmod(0o600)

    changed = original != SECURE_SCRIPT
    if changed:
        if backup:
            backup_path = script_path.with_suffix(
                script_path.suffix + f".bak-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            )
            shutil.copy2(script_path, backup_path)
        script_path.write_text(SECURE_SCRIPT)
        script_path.chmod(0o755)
    return changed, backup_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Move hardcoded check_limits.sh secrets into a private env file")
    parser.add_argument("--script", type=Path, default=DEFAULT_SCRIPT_PATH)
    parser.add_argument("--env", type=Path, default=DEFAULT_ENV_PATH)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)

    changed, backup_path = secure_check_limits(args.script, args.env, backup=not args.no_backup)
    if changed:
        print(f"changed backup={backup_path}")
    else:
        print("already-secure")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
