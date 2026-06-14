# Hermes Retek Session Handoff

Date: 2026-06-15

## Current State

Working branch in this workspace:

```text
ops-safe-restart-speed
```

GitHub branch created for this work:

```text
ops-safe-restart-speed-g3-rlm-20260615
```

GitHub URL:

```text
https://github.com/megawinrar/Hermes_Retek/tree/ops-safe-restart-speed-g3-rlm-20260615
```

Code baseline before this handoff update:

```text
1e8b0b4 test: expand context and rlm coverage
```

Key commits in this session:

- `9af196c feat: add bounded parallel orchestration policy`
- `9278225 refactor: isolate parallel orchestration policy`
- `55d991f feat: audit live bot activity in sqlite`
- `6007481 feat: store redacted bot outputs in audit`
- `64bdcb3 docs: add architecture rollout plan`
- `c8a04a8 feat: add rlm memory and agent orchestration primitives`
- `2b08961 feat: add authenticated browser session skill`
- `697cb7b feat: write process summaries to rlm store`
- `ed77da3 refactor: isolate process rlm memory writer`
- `55b9b9e feat: add supervisor-gated agent workspace lifecycle`
- `9cf4894 feat: add durable startup context packs`
- `1e8b0b4 test: expand context and rlm coverage`

## What Was Implemented

RLM-lite memory:

- `scripts/rlm_store.py`
- `scripts/process_rlm_memory.py`
- process summaries, Bot1 output, Bot2 reviews, human-gate records, and browser-skill usage records;
- non-blocking RLM write outcome: failures become `rlm_write_failed` process events.

Bounded orchestration:

- `scripts/parallel_orchestration.py`
- max parallel agents by task level;
- per-agent timeout/token budget;
- BotHub max parallel calls, requests/minute, cooldown;
- isolated workspace policy and single-writer SQLite/state rules.

g3-inspired workspace lifecycle:

- `scripts/agent_workspace.py`
- safe workspace paths under `/opt/data/agent_workspaces/{process_id}/{agent_id}`;
- create/list/status/cleanup;
- `set-status`, `accept`, `discard`;
- `accept` is Supervisor-gated and never auto-merges.

Bot1/Bot2 durable startup context packs:

- `scripts/process_context_pack.py`
- fresh Bot1/Bot2 sessions now receive a compact durable context pack instead
  of relying on long chat history;
- pack includes task, acceptance, route/risk, role skills, workspace snapshot,
  previous attempts, Bot2 required fixes/risks, human decision state, RLM
  records, and single-writer safety rules;
- pack budget defaults to 30% of the requested token budget, bounded from 120
  to 800 tokens;
- Supervisor logs `durable_context_pack_built` and `context_engineer`
  `session_startup` records in process SQLite;
- Bot1/Bot2 prompt formatting includes `startup_context_pack`;
- cookie/session-like values are redacted before prompt/event storage;
- live prompt builders redact task, acceptance, Bot1 output, revision input,
  route audit payloads, and JSON repair input before LLM calls.

Skills and browser work:

- `skills/hermes-browser/SKILL.md`
- `scripts/hermes_browser_session.py`
- persistent profile/session directory;
- artifacts, screenshots, HTML source, cookies file;
- cookie values are not printed by default;
- runner errors are redacted before stderr.

Secret/runtime safety:

- `scripts/secret_patterns.py`
- `scripts/secret_vault.py`
- generic prefixed token redaction;
- Bot1/Bot2 activity logs are redacted.

## Tests Before GitHub Push

Local verification before server deploy:

```text
pytest: 256 passed
coverage over scripts/: 64%
process_context_pack.py coverage: 97%
rlm_store.py coverage: 73%
secret_patterns.py coverage: 100%
secret_audit on current tracked target paths: 0 findings
```

Focused coverage added:

- RLM store add/search/context pack;
- process-to-RLM sidecar success, disabled mode, failure events, redaction;
- process `continue` writes RLM records after human yes;
- browser cookies stdout safety;
- browser runner stderr redaction;
- browser SKILL.md CLI examples parse;
- on-demand browser skill cache isolation;
- workspace lifecycle accept/discard gates;
- secret vault permissions/redaction;
- bounded parallel orchestration policy.
- durable Bot1/Bot2 startup context pack construction;
- prompt inclusion of startup context without cookie/session leaks;
- run/continue process logging of context pack startup.
- live prompt redaction for task/acceptance/Bot1 output/revision/repair inputs;
- RLM store get/kind/artifact fallback/metadata/CLI add+pack edge coverage;
- shared secret tuple payload and cookie/session assignment redaction coverage.

## Server Deploy

Production path:

```text
/opt/hermes-assistant
```

Important constraint:

```text
Do not switch /opt/hermes-assistant away from branch custom without explicit user approval.
```

Server remained on:

```text
branch=custom
head=908cd72
```

Because `/opt/hermes-assistant` had many local modified/untracked files, the
deploy was done as a file overlay, not as `git pull`, merge, reset, or branch
switch.

Files deployed:

- 31 files from `ops-safe-restart-speed-g3-rlm-20260615`;
- deployed through staging:

```text
/home/yc-user/hermes-deploy-staging-20260614T220542Z
```

Backup before sudo overlay:

```text
/home/yc-user/hermes-file-deploy-backups/sudo-20260614T220614Z
```

Server checks after deploy:

```text
syntax_ok=12
secret_audit on deployed files: 0 findings
agent_workspace accept smoke: accepted
process_orchestrator RLM smoke: approved
```

Full `pytest` was not run on the server because `pytest` is not installed there.
Local full suite did pass before deploy.

Known server risk:

- A broad secret audit over all server `custom/` still reports old findings in:
  - `custom/config/.env.example`
  - `custom/config/config.yaml`
- These findings are from existing server files, not from the newly deployed
  files. They should be cleaned/rotated separately.

## Bot1/Bot2 Session Continuity Decision

Do not make Bot1 and Bot2 start from a truly empty context every time.

Also do not keep one giant infinite chat session forever. That is exactly how
context compaction loses details and how old assumptions leak into new work.

Use this model:

```text
new execution session + durable context pack
```

Meaning:

- Bot1 starts a fresh bounded execution session for a task or revision;
- Bot2 starts a fresh bounded review session for each quality gate;
- both receive a compact context pack from SQLite/RLM/process events;
- raw long chat history stays in durable logs/artifacts, not in the live prompt;
- secrets are passed as vault refs, not raw values;
- Supervisor remains the single writer for shared state.

The context pack for Bot1 should include:

- task and acceptance contract;
- route and risk level;
- selected skills;
- workspace path/status;
- relevant previous attempts;
- required fixes from Bot2;
- current test/evidence requirements;
- relevant RLM records.

The context pack for Bot2 should include:

- task and acceptance contract;
- Bot1 result/diff/evidence;
- test output;
- risk notes;
- previous Bot2 verdicts if this is a retry;
- human decision state if present;
- exact approval/rejection criteria.

This is close to the useful part of `g3`: g3 has a coach/player loop and
session/workspace metadata, plus context thinning/compaction instead of simply
throwing everything away. Hermes should keep that shape, but with stronger
Supervisor/Bot2 gates.

Implemented status as of `1e8b0b4`:

- initial `run` builds startup packs for Bot1/Bot2 when Bot2/review is required
  or RLM is enabled;
- `continue` after human YES rebuilds packs from process SQLite/RLM and passes
  Bot2 required fixes back into Bot1;
- L0/simple L1 paths do not load memory by default unless RLM is enabled;
- production `/opt/hermes-assistant` was not touched in this continuation.

## Next Session First Prompt

Use this in the next Codex chat:

```text
Продолжи Hermes Retek с docs/16_session_handoff.md. Рабочая ветка ops-safe-restart-speed. GitHub ветка с текущей работой: ops-safe-restart-speed-g3-rlm-20260615. Не переключай production /opt/hermes-assistant с ветки custom без явного разрешения. Сначала проверь git status, затем продолжай с Bot1/Bot2 durable context pack: старт новых коротких сессий из RLM/process SQLite, без бесконечного чата и без потери контекста.
```

## Recommended Next Work

1. Add compact context-pack builder for Bot1/Bot2 session startup:
   - process state;
   - RLM records;
   - last Bot1/Bot2/human events;
   - selected skills;
   - workspace metadata.
2. Add compaction records:

```text
kind=compaction
tags=context,compaction,{process_id}
metadata={source_event_ids, trigger_percent, token_budget}
```

3. Add subcall records for parallel agents:

```text
kind=subcall
metadata={parent_process_id, child_agent_id, depth, timeout, token_budget}
```

4. Clean old server-side secret placeholders/findings in `custom/config`.
5. Decide whether to install a lightweight test runner on the server or keep
   server verification to syntax/smoke checks.
