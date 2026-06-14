# Hermes Retek Architecture Rollout Plan

Date: 2026-06-15

## Goal

Turn the Bot1/Bot2 prototype into a controlled multi-agent runtime without
losing production safety:

- Bot1 does implementation work.
- Bot2 reviews gates and can force repair or human approval.
- Supervisor owns phase transitions, SQLite state, resource locks, deploys, and
  Telegram decisions.
- Parallel agents may gather evidence and verify results, but they do not write
  shared state directly.

## Server Operations Baseline

Safe server cleanup may run while agents are active if it only removes caches,
old backups, unused Docker images, temporary pytest folders, and journal growth.

Full restart remains gated. If `scripts/hermes_safe_restart.sh --dry-run` sees
active `awaiting_human_decision`, `return_to_bot1`, or other in-flight process
states, the restart must be postponed or explicitly approved by the operator.

Production checkout rule remains unchanged:

```text
/opt/hermes-assistant stays on branch custom.
Do not switch production to ops-safe-restart-speed without explicit approval.
```

## Roles

Architect:
Owns contracts, ADRs, rollout sequence, and acceptance criteria.

Security/Vault Agent:
Owns secret intake, redaction, vault references, permission checks, and audit
tests. It never writes raw passwords, cookies, or tokens to memory, RAG/RLM,
SQLite reports, process logs, Markdown docs, or Git history.

Context Engineer:
Owns token/context accounting, compaction triggers, artifact retrieval, and
history summaries.

Orchestrator Engineer:
Owns bounded parallelism, isolated workspaces, resource locks, write queues, and
single-writer SQLite discipline.

Bot1 Developer:
Implements the approved change in an isolated workspace and reports evidence.

Tester:
Runs focused and full tests, records command evidence, and checks rollback paths.

Bot2 Reviewer:
Checks plan, implementation, verification output, missing tests, and risk. Bot2
returns machine-readable verdicts only; it does not mutate runtime state.

DevOps Operator:
Deploys targeted files, validates server state, and performs restart only after
safe-restart gates pass or the operator explicitly accepts risk.

## Bounded Parallel Agent Orchestration

Parallelism is allowed for discovery and verification only:

```text
Discovery fan-out: allowed.
Execution writes: single writer only.
Verification fan-out: allowed.
Approval and state transitions: Supervisor only.
```

Current executable policy lives in `scripts/parallel_orchestration.py`:

- L1: no parallel agents.
- L2: verification helper only, timeout 60s, budget 700 tokens.
- L3: max 3 agents, timeout 120s, budget 900 tokens.
- L4: max 5 agents, timeout 150s, budget 1200 tokens.
- Verification cap: 3 agents by default.
- BotHub cap: 2 concurrent calls, 12 requests/minute, 250ms cooldown.
- Execution writes: one active writer per protected resource.

Each agent works in:

```text
/opt/data/agent_workspaces/{process_id}/{agent_id}
```

Only Supervisor can merge results into the shared workspace. SQLite writes must
flow through Supervisor or Tool Gateway. Agents can return evidence, patches, and
recommendations, but not perform production writes directly.

## g3 Ideas To Borrow

g3's useful idea is not "more agents"; it is separation of duties:

- A coach-like controller keeps the task direction and decides when to stop.
- Worker sessions produce concrete artifacts.
- Tool access is centralized.
- Session continuation is explicit instead of relying on a long prompt forever.
- Work can happen in isolated Git worktrees and then be merged by the controller.

For Hermes this maps to:

- Supervisor is the coach.
- Bot1 and specialized agents are workers.
- Bot2 is the independent reviewer.
- Tool Gateway is the central tool policy.
- SQLite and artifact snapshots are the continuation layer.
- Agent workspaces are isolated worktrees/copy-on-write directories.

## RLM Ideas To Borrow

RLM is useful as a context runtime pattern, not as a secret store.

Hermes should store durable facts as artifacts and query them back when needed:

- plans;
- evidence;
- Bot2 verdicts;
- test outputs;
- timing reports;
- Telegram decisions;
- compact session summaries.

Raw secrets are excluded from this layer. Any retrieval result that enters the
prompt must already be redacted.

## Context Budget Policy

Use staged thresholds:

- 30%: start writing compact facts and artifact references.
- 50%: summarize old evidence and keep only links plus decisions in prompt.
- 70%: stop new discovery unless the current phase requires it.
- 80%: force checkpoint before more agent work.

Context size is estimated from token counters where the provider exposes them.
When no provider counter exists, Hermes uses a conservative text estimate:

```text
estimated_tokens = ceil(characters / 4)
```

The estimate is stored with each process event so compaction decisions are
auditable.

## Telegram Secret Intake

Telegram is not end-to-end encrypted for bot chats, so the policy is:

- allow only private chats from an operator allowlist;
- accept secrets only in an explicit secret-intake command;
- delete the source Telegram message when the platform allows it;
- immediately write the value to server vault;
- return a `secret://name/field` reference;
- log only metadata and redacted previews;
- never put raw values into SQLite, Bot1/Bot2 messages, memory, reports, docs, or
  Git.

Initial implementation uses a permission-protected server file vault:

```text
/var/lib/docker/volumes/hermes-data/_data/.secrets/{name}/{field}
```

Directories are `0700`, secret files are `0600`, and names are path-safe. A later
phase can wrap the same `secret://...` API with OS keychain, KMS, or age/sops
encryption without changing Bot1/Bot2 contracts.

## Rollout Sequence

1. Add vault API and tests.
2. Add Telegram secret-intake command that writes vault refs only.
3. Add SQLite audit events for secret-intake metadata without values.
4. Add context ledger events and 30/50/70/80 threshold tests.
5. Add agent workspace allocator and cleanup policy.
6. Add single-writer queue tests that prove agents cannot write SQLite directly.
7. Add BotHub rate limiter integration tests.
8. Add server rollout checklist and deploy targeted files.

## Acceptance

- Full local test suite passes.
- Server targeted tests pass after deploy.
- `scripts/secret_audit.py --current --paths scripts configs custom docs --json`
  emits no production secret values.
- Bot1/Bot2 activity is visible in SQLite as redacted audit events.
- Telegram approval buttons still produce the expected backend decisions.
- Safe restart dry-run passes before any real restart, unless the operator
  explicitly accepts active-process interruption.
