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
2b93515 docs: record coverage refactor status
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
- `2b93515 docs: record coverage refactor status`
- `270df9f test: cover gateway supervisor and context budget`

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

## Current Test Baseline

Latest local verification in this workspace:

```text
pytest: 273 passed
coverage over production/custom/scripts code: 74%
coverage full report including tests: 83%
context_budget.py coverage: 96%
supervisor_common.py coverage: 91%
supervisor_run.py coverage: 73%
supervisor_status.py coverage: 53%
supervisor_task.py coverage: 55%
tool_gateway.py coverage: 76%
custom/tools/hermes_process_tool.py coverage: 79%
process_orchestrator.py coverage: 70%
process_rlm_memory.py coverage: 99%
rlm_store.py coverage: 73%
```

Additional narrow coverage added after the previous baseline:

- direct in-process `tool_gateway` tests for command classification, protected
  write detection, resource mapping, `check`, `run`, denial paths, and released
  resource locks;
- fail-closed `tool_gateway` handling for dangerous commands linked to a
  missing Supervisor task;
- in-process Supervisor CLI tests for create/list/show/run, Bot2 approval,
  Bot2 request-changes escalation, human decision notification, and plain list
  output;
- `context_budget` in-process CLI tests for text/file inputs, conflicting input
  rejection, missing input rejection, invalid token counts, and raw prompt
  non-disclosure.
- `hermes_process_tool` default RLM flags for `run` and `continue`;
- explicit `rlm_enabled=false` opt-out and `rlm_enabled=null` default fallback;
- custom RLM store path plumbing through the tool adapter;
- `kontur-parser` skill selection for supplier/browser routes.

## GitHub Stop Marker

Previous stop marker:

```text
handoff-20260615-coverage70-server
```

Current stop marker after this continuation:

```text
handoff-20260615-big-context-policy
```

This marker points to the GitHub branch state after:

- coverage was raised to 70%;
- gateway/supervisor/context-budget narrow tests were added;
- the latest reviewed files were overlaid onto the Yandex server without
  switching production away from `custom`.

The current marker additionally points to:

- live RLM enablement through `hermes_process_tool`;
- `kontur-parser` skill and manifest/test coverage;
- server-side Kontur browser script credential sanitization;
- `/opt/data/rlm_store.db` smoke write;
- server overlay/restart notes for the Docker file bind mount.
- big-task context policy: `max_tokens` default 6000, manual cap 20000,
  normal context pack 50% capped at 3000, expanded context pack 70% capped at
  5000 for L4, retries, high-risk agent work, Kontur/supplier, deploy,
  migrations, and huge tasks.

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

Full `pytest` was not run during the first server deploy. Local full suite did
pass before deploy.

Latest server overlay after coverage work:

```text
server: yc-user@89.169.142.160
production path: /opt/hermes-assistant
server branch: custom
server head: 908cd72
local source commit: 270df9f test: cover gateway supervisor and context budget
staging: /home/yc-user/hermes-deploy-staging-20260614T224809Z
backup: /home/yc-user/hermes-file-deploy-backups/sudo-20260614T224809Z
```

Files overlaid in that deploy:

- `docs/16_session_handoff.md`
- `scripts/tool_gateway.py`
- `tests/test_context_budget.py`
- `tests/test_supervisor_smoke.py`
- `tests/test_tool_gateway.py`

Verification after latest overlay:

```text
sha256 local/server match: yes, for all 5 overlaid files
syntax compile without pyc writes: syntax_compile_ok 6
server focused pytest: 30 passed
server pytest warning: could not create .pytest_cache under /opt/hermes-assistant
tool_gateway safe smoke: allowed=true, reason=command_not_dangerous
tool_gateway dangerous smoke: allowed=false, reason=missing_supervisor_task_id, exit=2
secret_audit on overlaid paths: 0 findings
containers: hermes-agent Up, hermes-yandex-proxy Up/healthy
restart performed: no
```

## Latest Continuation: RLM Live Enablement and Kontur Skill

Implemented after the `handoff-20260615-coverage70-server` marker:

- `custom/tools/hermes_process_tool.py`
  - defaults RLM writes on for `run` and `continue`;
  - passes `rlm_store` and `rlm_enabled` into in-process namespaces;
  - sets subprocess env defaults:
    - `HERMES_RLM_STORE_PATH=/opt/data/rlm_store.db`
    - `HERMES_RLM_ENABLED=1`
  - keeps `rlm_enabled=false` as the safe opt-out.
- `tests/test_hermes_process_tool.py`
  - covers default RLM flags;
  - covers explicit disable;
  - covers `null`/missing default behavior;
  - covers custom store path;
  - isolates in-process RLM test writes into a temp SQLite file.
- `skills/kontur-parser/SKILL.md`
  - adds a narrow Kontur domain skill over `hermes-browser`;
  - documents `/opt/data/rebrowser` state, cookies, scripts, artifacts, and
    RLM lessons;
  - explicitly forbids hard-coded/printed passwords, cookie values, tokens, and
    `auth.sid`.
- `skills/manifest.json`
  - adds `kontur-parser` as an on-demand supplier/browser/auth skill.
- `tests/test_skill_index.py`
  - verifies supplier browser tasks select both `hermes-browser` and
    `kontur-parser`.

Local verification after these changes:

```text
pytest: 273 passed
focused RLM/tool/skill tests: 39 passed
secret_audit current tree: 0 findings
coverage production/custom/scripts: 74%
coverage full report including tests: 83%
```

Server operational fixes in the same continuation:

- sanitized raw Kontur credentials out of these runtime JS files:
  - `/opt/data/rebrowser/debug-dates.js`
  - `/opt/data/rebrowser/debug-leak.js`
  - `/opt/data/rebrowser/debug-login.js`
  - `/opt/data/rebrowser/dump-grid.js`
  - `/opt/data/rebrowser/login-kontur.js`
  - `/opt/data/rebrowser/search-batches.js`
  - `/opt/data/rebrowser/search-kontur.js`
- runtime scripts now use existing cookies first and only read
  `KONTUR_EMAIL`/`KONTUR_PASSWORD` from environment when explicitly set;
- `cookies.json` and `session-state.json` are mode `0600`;
- runtime JS syntax check passed with `node --check`;
- deleted 19 macOS `._*` AppleDouble files from `/opt/hermes-assistant`;
- kept a deletion list at:

```text
/home/yc-user/hermes-file-deploy-backups/appledouble-clean-20260615T023237Z/appledouble-files.txt
```

Server overlays in this continuation:

```text
RLM tool backup:
/home/yc-user/hermes-file-deploy-backups/rlm-tool-20260615T022424Z

Kontur skill backup:
/home/yc-user/hermes-file-deploy-backups/kontur-skill-20260615T023046Z
```

Files overlaid on `/opt/hermes-assistant`:

- `custom/tools/hermes_process_tool.py`
- `tests/test_hermes_process_tool.py`
- `skills/manifest.json`
- `skills/kontur-parser/SKILL.md`
- `tests/test_skill_index.py`

Files overlaid on live skill storage:

- `/opt/data/skills/manifest.json`
- `/opt/data/skills/kontur-parser/SKILL.md`

Server verification after overlay:

```text
server branch: custom
server head: 908cd72
server focused pytest after RLM overlay: 27 passed
server focused pytest after skill overlay: 36 passed
server pytest warning: could not create .pytest_cache under /opt/hermes-assistant
server secret_audit on changed files: 0 findings
hermes-agent: Up
hermes-yandex-proxy: Up/healthy
```

Important Docker note:

- `/opt/hermes-assistant/custom/tools/hermes_process_tool.py` is file-mounted
  into the container as `/opt/hermes/tools/hermes_process_tool.py`;
- replacing the host file with `install` changes the host inode, so the running
  container kept the old mounted inode;
- `hermes-agent` was restarted briefly to rebind the updated file;
- a second brief restart was done after adding `kontur-parser` so the gateway
  reloads the skill list.

RLM live smoke after restart:

```text
tool path: /opt/hermes/tools/hermes_process_tool.py
rlm_store: /opt/data/rlm_store.db
has_rlm_enabled: true
execute_ok: true
status: approved
process_id: proc-20260615-022720-2e15af
rlm_records_after_smoke: 2
latest record kinds: process_summary, bot_output
```

Kontur runtime status observed before fixes:

- cookies were alive and Kontur Grid opened;
- Hermes found `2 796` закупок for `Д16Т`;
- Excel export did not download a file;
- Python parsing failed because `bs4` was missing in the container;
- logs showed `skill 'kontur-parser' not found`; this is fixed by the new skill;
- logs also showed BotHub read timeouts/interruptions and
  `Memory is not available`, which are still separate runtime issues.

## Latest Continuation: Big-Task Context Policy

Decision:

- Hermes must accept complex tasks; refusing them because the prompt is large is
  not acceptable.
- The safe pattern is not one infinite raw chat. Use a fresh execution session
  plus a larger dry structured context pack from SQLite/RLM/events/artifacts.
- Ordinary tasks stay compact. Complex/retry/high-risk tasks get expanded
  context automatically.

Implemented policy:

```text
default process max_tokens: 6000
manual max_tokens cap through hermes_process_tool: 20000
normal startup context pack: 50% of max_tokens, capped at 3000
expanded startup context pack: 70% of max_tokens, capped at 5000
default context pack when max_tokens is absent: 3000
```

Expanded context triggers:

- `phase != initial`, especially `human_continue`;
- L4 or human-gated routes;
- high-risk review routes with agents;
- `supplier_price_deadline_analysis`;
- `code_or_deploy_project`;
- `database_migration_change`;
- `git_write_or_deploy`;
- task/acceptance over 4000 chars;
- Kontur/zakupki/Excel/deploy/migration keywords.

Files changed:

- `scripts/process_context_pack.py`
  - added expanded context detection and route-aware budget calculation;
  - added env overrides:
    - `HERMES_CONTEXT_PACK_RATIO`
    - `HERMES_CONTEXT_PACK_EXPANDED_RATIO`
    - `HERMES_CONTEXT_PACK_DEFAULT_TOKENS`
    - `HERMES_CONTEXT_PACK_MAX_TOKENS`
    - `HERMES_CONTEXT_PACK_EXPANDED_MAX_TOKENS`
    - `HERMES_CONTEXT_PACK_BIG_TASK_CHARS`
- `scripts/process_orchestrator.py`
  - default `--max-tokens` is now `HERMES_PROCESS_MAX_TOKENS` or 6000;
  - route-aware context pack budget is used for run/continue;
  - Bot2 verdict budget increased to 1600 for L2 and 2200 for L3/L4.
- `custom/tools/hermes_process_tool.py`
  - default `max_tokens` is now 6000;
  - schema/manual cap is now 20000 through `HERMES_PROCESS_MAX_TOKEN_LIMIT`.
- `configs/token_governor.yaml`
  - documents the structured context pack policy.

Verification:

```text
focused context/tool/orchestrator tests: 68 passed
full pytest: 275 passed
secret_audit current tree: 0 findings
```

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
- `hermes_process_tool` now enables RLM for live `run`/`continue` by default;
- production `/opt/hermes-assistant` remained on branch `custom`; only file
  overlays and brief `hermes-agent` restarts were performed.

Additional ops status as of `5f32a78`:

- BotHub was paid and verified again from inside `hermes-agent`;
- `hermes-agent` was restored from temporary Yandex proxy fallback to
  `OPENAI_BASE_URL=https://openai.bothub.chat/v1` and
  `OPENAI_MODEL=deepseek-v4-flash`;
- production `/opt/hermes-assistant` still remained on branch `custom`
  (`908cd72`);
- `scripts/hermes_timing_report.py` now emits a "Что чинить первым" section
  and JSON guardrail metrics for first flush, max api calls, max tool turns,
  delegate latency, and operational action count;
- timing report parsing now supports Docker `--timestamps` ISO log prefixes;
- `scripts/capture_hermes_docker_logs.sh` snapshots `hermes-agent` Docker logs
  into `/opt/data/logs/gateway.log`, `/opt/data/logs/agent.log`, and
  `/opt/data/logs/hermes-agent-docker.log`;
- server cron runs the snapshot every 5 minutes and writes stderr to
  `/opt/data/logs/hermes-log-capture.log`;
- local test suite passed: `278 passed`;
- server focused timing tests passed: `11 passed`.

Runtime guardrails status as of `bf0563d + runtime performance continuation`:

- `/opt/data/config.yaml` was backed up to
  `/opt/data/config.yaml.backup-runtime-guardrails-20260615T092020Z`;
- parent Hermes loop was capped with `agent.max_turns: 16`;
- user-visible progress cadence was tightened with
  `agent.gateway_notify_interval: 30` and
  `agent.gateway_timeout_warning: 300`;
- subagent/delegate guardrails were tightened:
  `delegation.max_concurrent_children: 2`,
  `delegation.child_timeout_seconds: 120`,
  `delegation.max_iterations: 8`;
- gateway streaming was enabled:
  `streaming.enabled: true`, `streaming.transport: auto`,
  `streaming.edit_interval: 1.0`, `streaming.buffer_threshold: 24`;
- `hermes_safe_restart.sh` initially blocked restart because of stale
  `awaiting_human_decision`/`return_to_bot1` records from 2026-06-14; restart
  was forced with reason `runtime_guardrails_stale_waiting_tasks` after
  verifying those records were not live turns;
- server `hermes-agent` restarted successfully and still runs on BotHub;
- added repeatable repo script `scripts/runtime_guardrails.py` plus
  `tests/test_runtime_guardrails.py`;
- added `scripts/patch_gateway_early_ack.py` plus
  `tests/test_patch_gateway_early_ack.py`;
- added `scripts/patch_delegate_subcall_rlm.py` plus
  `tests/test_patch_delegate_subcall_rlm.py`;
- added `rlm_store.add_subcall_record(...)` and tests for durable child-agent
  lifecycle records;
- `scripts/hermes_safe_restart.sh` now treats `running` as always active, but
  only treats `awaiting_human_decision` and `return_to_bot1` as active while
  they are fresh; default TTL is `21600` seconds and can be changed with
  `--waiting-ttl-seconds` or `HERMES_RESTART_WAITING_TTL_SECONDS`;
- full early Telegram ack was patched into the live gateway runtime before the
  first LLM/tool loop; env controls:
  `HERMES_TELEGRAM_EARLY_ACK_ENABLED` and
  `HERMES_TELEGRAM_EARLY_ACK_TEXT`;
- child-agent subcall lifecycle records were patched into the live
  `delegate_task` runtime; env controls:
  `HERMES_RLM_SUBCALL_ENABLED` and `HERMES_ASSISTANT_SCRIPTS`;
- local full test suite passed after this continuation: `289 passed`;
- local coverage over `scripts/*.py` and `custom/**/*.py`: `74%`;
- server focused tests passed: `19 passed`;
- server RLM subcall smoke wrote `kind=subcall` to `/opt/data/rlm_store.db`;
- server `hermes-agent` restarted without `--force`; the stale 2026-06-14
  waiting records no longer blocked restart;
- production `/opt/hermes-assistant` remained on branch `custom` at `908cd72`;
- Hermes still runs on BotHub:
  `OPENAI_BASE_URL=https://openai.bothub.chat/v1`,
  `OPENAI_MODEL=deepseek-v4-flash`.

Remaining runtime work:

- Watch the next real Telegram turn and confirm the early ack appears before
  the first BotHub/model response.
- Watch the next real `delegate_task` turn and confirm real child-agent
  lifecycle rows are written as `kind=subcall` in `/opt/data/rlm_store.db`.
- The early ack and delegate subcall hooks are runtime patches under
  `/opt/hermes`; if the container is recreated from image rather than
  restarted, re-run the patchers from `/opt/hermes-assistant/scripts`.

## Next Session First Prompt

Use this in the next Codex chat:

```text
Продолжи Hermes Retek с docs/16_session_handoff.md. Рабочая ветка ops-safe-restart-speed. GitHub ветка с текущей работой: ops-safe-restart-speed-g3-rlm-20260615. Не переключай production /opt/hermes-assistant с ветки custom без явного разрешения. Сначала проверь git status, затем проверь сервер: hermes-agent/hermes-yandex-proxy, что Hermes на BotHub (`OPENAI_BASE_URL=https://openai.bothub.chat/v1`, `OPENAI_MODEL=deepseek-v4-flash`), runtime guardrails активны, ранний Telegram ack patch marker есть в `/opt/hermes/gateway/platforms/base.py`, subcall RLM patch marker есть в `/opt/hermes/tools/delegate_tool.py`, `/opt/data/rlm_store.db` жив, skill kontur-parser есть, и big-task context policy активна. Затем наблюдай следующий реальный Telegram turn: должен быть быстрый ack до первого LLM, а при delegate_task должны появляться `kind=subcall` записи. Потом продолжай missing deps, RLM lessons, Kontur workflow и timing report comparison.
```

## Recommended Next Work

1. Re-run the Kontur Excel export path and capture exact selectors/endpoints
   into RLM and `kontur-parser` after a successful export.
2. Add compaction records:

```text
kind=compaction
tags=context,compaction,{process_id}
metadata={source_event_ids, trigger_percent, token_budget}
```

3. Clean old server-side secret placeholders/findings in `custom/config`.
4. Decide whether to install a lightweight test runner on the server or keep
   server verification to syntax/smoke checks.
5. Install or vendor the browser parsing dependencies Hermes tried to use
   (`bs4`, and possibly `requests`, `pandas`, `openpyxl`) only if the next
   Kontur workflow still needs Python HTML/Excel parsing.
6. Investigate runtime `Memory is not available` in Hermes logs now that RLM
   process memory is active through `hermes_process_tool`.
