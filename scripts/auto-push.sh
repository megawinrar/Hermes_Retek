#!/bin/bash
set -euo pipefail
# Legacy autosync is fail-closed unless Supervisor explicitly approved it.

if [ "${HERMES_ALLOW_AUTOPUSH:-0}" != "1" ] || [ "${HERMES_SUPERVISOR_APPROVED:-0}" != "1" ]; then
  echo "auto-push blocked: requires HERMES_ALLOW_AUTOPUSH=1 and HERMES_SUPERVISOR_APPROVED=1" >&2
  exit 2
fi

REPO_DIR="${HERMES_AUTOPUSH_REPO:-/opt/data}"
BRANCH="${HERMES_AUTOPUSH_BRANCH:-main}"
cd "$REPO_DIR"

python3 - <<'PY'
import pathlib
import re
import sys

patterns = [
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
]
offenders = []
for path in pathlib.Path(".").rglob("*"):
    if path.is_file() and ".git" not in path.parts:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in patterns):
            offenders.append(str(path))
if offenders:
    print("secret scan failed:", ", ".join(offenders[:10]), file=sys.stderr)
    sys.exit(3)
PY

git checkout "$BRANCH"
git add -A
if git diff --cached --quiet; then
  echo "No changes to push"
  exit 0
fi
git commit -m "Hermes approved autosync $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push origin "$BRANCH"
