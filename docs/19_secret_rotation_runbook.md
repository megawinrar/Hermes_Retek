# Secret Rotation Runbook

Date: 2026-06-13

## Scope

This runbook closes the repository-side part of P0 secret cleanup.

The current tracked production scripts/configs must stay free of hardcoded
secrets. Any secret that was ever committed to git history must still be treated
as compromised until it is revoked in the provider UI and replaced in the server
secret store.

## Audit Commands

Current tracked files:

```bash
scripts/secret_audit.py --current --json
```

Full reachable git history:

```bash
scripts/secret_audit.py --history --json
```

Focused production surfaces:

```bash
scripts/secret_audit.py --current --paths scripts configs AGENTS.md skills --json
scripts/secret_audit.py --history --paths scripts configs AGENTS.md skills --json
```

The audit output is intentionally metadata-only. It reports the pattern name,
path, line, scope, and optional commit, but never prints matched secret values.
Tests, docs, and Markdown skill references are treated as fixture/example scope
by default to keep production checks actionable. Include them explicitly during
forensic review:

```bash
scripts/secret_audit.py --history --include-fixtures --json
```

## Rotation Steps

1. Revoke the old Bothub/API key in the external provider account.
2. Create a new key in the provider account.
3. Put the new key outside git on the server, for example:

```bash
sudo install -d -m 700 -o root -g root /var/lib/docker/volumes/hermes-data/_data/.secrets
sudo install -m 600 -o root -g root /dev/stdin /var/lib/docker/volumes/hermes-data/_data/.secrets/bothub_api_key
```

4. Verify the healthcheck reads from env or the secret file:

```bash
sudo BOTHUB_API_KEY_FILE=/var/lib/docker/volumes/hermes-data/_data/.secrets/bothub_api_key scripts/check_api_limits.sh
```

5. Run the secret audit again and confirm no new secret values were written to
   files, logs, reports, or notification payloads.

## Server Rollout Notes

- Do not place provider keys in git remotes, shell history, process reports, or
  Markdown docs.
- Do not rewrite the live server checkout while it has local operational drift.
  Install audited scripts as targeted file updates with backup.
- Do not revoke the only working GitHub or provider token until a replacement is
  installed and smoke-tested.
- `scripts/secret_audit.py` can be copied to `/opt/hermes-assistant/scripts/`
  and run from that checkout without container restart.

## Acceptance

- Old provider key is revoked.
- New provider key exists only in env or root-readable secret file.
- `scripts/check_api_limits.sh` succeeds with the new secret source.
- `scripts/secret_audit.py --current --paths scripts configs AGENTS.md skills --json`
  returns zero findings.
- `scripts/secret_audit.py --history --json` is reviewed as evidence for
  provider-side revocation decisions, not as permission to keep old keys alive.
