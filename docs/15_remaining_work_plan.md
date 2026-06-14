# Hermes Retek Remaining Work Plan

Date: 2026-06-13

## Recommended Next Step

Next implementation target: `P1 Runtime-Compatible Hermes Integration`.

Reason:

- The live Telegram agent runs inside the `hermes-agent` Docker container.
- The Retek scripts are host-side Supervisor/Bot#2/process gates, not imported
  by the container.
- Future work should target the correct layer first: `AGENTS.md`/skills/config
  for agent behavior, scripts/configs for host-side gates, and `hermes-core`
  only for upstream-aware runtime changes.

Reference:

- `docs/17_hermes_runtime_integration.md`
- `docs/18_server_rollout_checklist.md`

## P0: Rotate Exposed Secrets

Status: repository audit tooling added; provider-side rotation still required.

Why:

- Current tracked files no longer contain the hardcoded key.
- But the old key existed in git history, so it must be treated as compromised.
- `scripts/secret_audit.py --current --json` currently returns zero findings.
- `scripts/secret_audit.py --history --paths scripts configs AGENTS.md skills --json`
  reports historical metadata-only findings for the old key.

Tasks:

- Rotate Bothub/API key in the external service.
- Store new key outside git, for example:

```bash
/var/lib/docker/volumes/hermes-data/_data/.secrets/bothub_api_key
```

- Restrict file permissions.
- Verify `scripts/check_api_limits.sh` works via env/secret file.
- Decide whether git history rewrite is needed or revoke/rotate is enough.
- Use `docs/19_secret_rotation_runbook.md` and `scripts/secret_audit.py` to
  audit current files and reachable git history without printing secret values.

Acceptance:

- old key revoked;
- new key absent from git/files/logs/reports;
- secret scan passes;
- API healthcheck passes.

## P1: Human Notification / Telegram DevLog

Status: next recommended code task.

Tasks:

- Add notification adapter for `process_orchestrator.py`.
- Reuse or extend `scripts/devlog.py` if possible.
- On `awaiting_human_decision`, send payload containing:
  - process id;
  - supervisor task id;
  - original task;
  - Bot#1 version;
  - Bot#2 version;
  - risk;
  - recommendation;
  - clear Yes/No semantics.
- Add dry-run mode for exact payload without network send.
- Add tests around payload shape and no-secret redaction.

Acceptance:

- unit/integration test validates notification payload;
- dry-run output can be inspected without Telegram;
- live human escalation records notification event;
- no secrets are sent.

## P1: Bot#2 Retry / Repair

Status: implemented in repository; server rollout still pending.

Current behavior:

- invalid Bot#2 JSON becomes `INVALID_BOT2_OUTPUT` and fail-closed.
- live dual Bot#1/Bot#2 path performs one strict JSON-only Bot#2 repair
  attempt when the first Bot#2 response is not machine-readable.
- repo-side `scripts/bot2_gate.py` mirrors the same one-repair/fail-closed
  contract for the host-side review gate.
- dry-run `INVALID_BOT2_OUTPUT` still fails closed and is used as a guardrail
  test case.
- dual-bot lab run metadata, stored messages, CLI previews, and Markdown reports
  are redacted before persistence.
- host-side Bot#2 gate storage, events, stdout, verdicts, and raw outputs are
  redacted before persistence/output.
- repaired live verdicts include `repair_attempted` and `repair_status`; failed
  repair attempts remain fail-closed and auditable.

Next behavior:

- deploy repo-side `scripts/bot2_gate.py` to the server only after review,
  backup, and smoke test using `docs/18_server_rollout_checklist.md`;
- verify server cron/manual commands use the repo copy instead of a drifting
  host-only script.

Acceptance:

- first invalid Bot#2 output triggers one retry;
- second invalid output cannot approve;
- embedded/log-contaminated JSON is rejected.

## P1: DevOps / Tool Gateway

Status: repository gateway and resource locks implemented; server rollout still pending.

Tasks:

- Create `scripts/tool_gateway.py`. Done.
- Require linked Supervisor approval for dangerous actions:
  - `git push`;
  - `git merge`;
  - deploy/release;
  - `docker restart`;
  - production config edits;
  - `sqlite UPDATE/DELETE`;
  - secret writes;
  - auth/payment/db/CI changes.
- Distinguish `approved_action=execute` from `approved_action=refuse`. Done.

Next behavior:

- route server DevOps commands through `scripts/tool_gateway.py run`;
- add server smoke checks before enabling gateway as an operational wrapper.

Acceptance:

- push/deploy/restart blocked without approval;
- approved refusal does not unlock DevOps;
- user override is explicit and audited.

## P1: Process State Machine

Status: supervisor task transitions and loop guard implemented.

Tasks:

- Formalize allowed transitions:
  - `created`
  - `running`
  - `approved`
  - `approved_refusal`
  - `awaiting_human_decision`
  - `return_to_bot1`
  - `accepted_by_user_override`
  - `failed`
  - `blocked`
- Add illegal transition tests. Done.
- Add loop guard for repeated Bot#1/Bot#2 cycles. Done.
- Add write/deploy resource locks. Done.

Next behavior:

- deploy `scripts/tool_gateway.py` and updated Supervisor schema to the server;
- wrap operational write/deploy commands with `tool_gateway.py run`.

Acceptance:

- no transition from `failed` to `approved` without new run;
- no DevOps from `approved_refusal`;
- repeated `REQUEST_CHANGES` eventually requires human.

## P2: Skills Index / Lazy Loading

Status: manifest and selector implemented in repository; runtime adoption pending.

Tasks:

- Build `skills/manifest.yaml` or `skills/index.json`. Done as `skills/manifest.json`.
- Include:
  - name;
  - description;
  - tags;
  - worker roles;
  - risk level;
  - script presence;
  - network/auth requirements;
  - load policy.
- Update Hermes role skills for Router, Supervisor, Bot#1, Tester, Bot#2, DevOps.
- Mark legacy GitLab/YandexGPT context.

Runner:

```bash
scripts/skill_index.py select --level L3 --role architect
```

Next behavior:

- wire Router/Supervisor prompts to load from `scripts/skill_index.py` output;
- keep DevOps/GitHub write skills behind explicit approval and `tool_gateway.py`.

Acceptance:

- Router loads only relevant skills;
- L0/L1 do not load heavy bundles;
- skill scripts require gateway approval.

## P2: Observability Dashboard

Status: repository dashboard summary and JSONL event tail implemented; server rollout still pending.

Tasks:

- Improve:

```bash
scripts/process_orchestrator.py show <process_id>
```

- Add process summary:
  - route;
  - actors;
  - state;
  - Bot#2 verdict;
  - human decision;
  - reports;
  - blocked reason.
- Add JSONL live event tail. Done.

Next behavior:

- use `scripts/process_orchestrator.py show <process_id>` during server smoke
  checks;
- use `scripts/process_orchestrator.py events <process_id>` for redacted JSONL
  event inspection.

Acceptance:

- user can watch process state in real time;
- reasons for approval/block/human-gate are clear;
- logs are redacted.

## Stage 2 Battle Suite

Status: deterministic repository runner implemented and passing locally.

Cases:

1. L0 status without LLM.
2. L1 rewrite without Bot#2.
3. L2 supplier prices/dates high-risk caution.
4. L3 SQLite -> Postgres migration plan.
5. L4 router code change with tests.
6. Adversarial push to main without tests/review.
7. Secret write attempt.
8. Bad Bot#2 JSON retry/fail-closed.
9. Human disagreement with visible Yes/No.
10. DevOps gate blocked before approval.

## Retek Real Task Dogfood Suite

Status: deterministic repository runner added for Retek-shaped tasks.

Runner:

```bash
scripts/real_task_suite.py --report-dir reports/real_tasks
```

Current cases:

1. Retek CRM supplier price/deadline/risk comparison.
2. Retek CRM SQLite -> Postgres migration plan.
3. Unsafe push/restart without tests.
4. Human-gate task containing a synthetic token-like value.
5. Secret write attempt through shell command.

Acceptance:

- Russian Retek tasks route to the expected process level/risk;
- unsafe deploy waits for human;
- secret writes are blocked before approval;
- synthetic token-like values are redacted from stdout, JSON, and Markdown reports.

## Bot#1/Bot#2 Supervisor Transcript

Status: transcript command added for process debugging.

Runner:

```bash
scripts/process_orchestrator.py transcript <process_id>
```

The transcript shows:

- Router route;
- Bot#1 result;
- Tester evidence;
- Bot#2 verdict and session id;
- Supervisor human-gate message, notification payload, and delivery mode;
- audit event names without exposing secrets.

## Live LLM Quality

Status: first live Bot#1/Bot#2 smoke passed; prompt-quality guardrails added.

Current behavior:

- Bot#1 and Bot#2 prompts preserve the exact Retek/`CRM Ретек` domain name.
- Bot#2 is told that Supervisor transcript is generated after its verdict, so
  absence of that future transcript inside Bot#1's answer is not sufficient by
  itself for `INSUFFICIENT_EVIDENCE`.
- Live LLM path stores redacted reports/transcripts through Supervisor tooling.

Next behavior:

- After provider key rotation, add a scheduled or manual live smoke suite.
- Tune Bot#2 to request concrete fixes without over-escalating valid L2 analysis.

Runner:

```bash
scripts/stage2_battle_suite.py
```

Acceptance:

- all unit tests pass;
- battle reports are saved;
- high-risk writes/deploys do not pass without approval;
- human-gate message is understandable;
- secrets do not appear in files, logs, reports, or notifications.

## Bot#1/Bot#2 Repair Loop Hardening

Status: live repair-loop runner added and production `process_orchestrator.py`
integration implemented for Bot#1 self-check and Bot#2 JSON repair.

Current behavior:

- Bot#2 `REQUEST_CHANGES` can return Bot#1 to a bounded revision loop.
- Bot#1 self-check runs before the next Bot#2 review and records a
  fix-closure checklist.
- Bot#2 invalid JSON is repaired once with a strict JSON-only prompt.
- Exhausted review cycles remain fail-safe by escalating to human decision.
- Process transcript exposes review cycles and fix-closure checklist.

Remaining follow-up fixes:

1. Promote the repair-loop branch through PR review and merge to `main`.
2. Run a server live smoke after merge using Level 3 migration and one L4
   deploy-pressure case.
3. Add a scheduled/manual live smoke command after provider key rotation.
4. Track provider/model failures with request id, model, phase, and repair
   status without logging secrets.
5. Add an operator runbook section for interpreting `APPROVE_WITH_EVIDENCE`,
   `REQUEST_CHANGES`, `INVALID_BOT2_OUTPUT`, and exhausted loop escalations.
6. Keep provider-side key rotation as the final external manual action before
   production hardening is considered complete.
