# Hermes Runtime Integration Contract

Date: 2026-06-13

## Purpose

This repository is a host-side governance package for the live Hermes Retek
agent. It should not be treated as a replacement for `hermes-core` or converted
into a new application layout unless that integration is explicitly planned and
tested.

The live server currently runs Hermes through Docker. The process layer in this
repository sits beside that runtime and controls review, routing, audit, and
human gates.

## Observed Runtime

Server path:

```text
/opt/hermes-assistant
```

Running containers:

```text
hermes-agent          -> Hermes messaging gateway
hermes-yandex-proxy   -> OpenAI-compatible LLM gateway and budget tracker
```

Hermes container command:

```text
hermes gateway run --accept-hooks
```

Container paths:

```text
/opt/hermes           -> upstream Hermes Agent code
/opt/data             -> HERMES_HOME, memory, skills, sessions, runtime state
```

Host-side process stores:

```text
/var/lib/docker/volumes/hermes-data/_data/bot2_review_store.db
/var/lib/docker/volumes/hermes-data/_data/dual_bot_lab_store.db
/var/lib/docker/volumes/hermes-data/_data/token_usage.db
```

## Component Boundary

```text
Telegram user
  -> hermes-agent container
     -> /opt/hermes upstream Hermes Agent
     -> /opt/data/AGENTS.md
     -> /opt/data/config.yaml
     -> mounted CRM read/audit tools
     -> model endpoint / Bothub or yandex-proxy

Host-side Hermes_Retek package
  -> scripts/task_router.py
  -> scripts/process_orchestrator.py
  -> scripts/skill_index.py
  -> scripts/supervisor_*.py
  -> scripts/bot2_gate.py on the live server
  -> configs/*.yaml
  -> docs/prompts/memories
  -> SQLite review/process stores
  -> optional docker exec into hermes-agent for controlled review runs
```

Important: `scripts/task_router.py` and `scripts/process_orchestrator.py` are
not mounted into `/opt/hermes` in the running container. They are host-side
control scripts.

## Runtime Skill Library

Hermes Retek keeps the large `skills/` tree behind a small manifest-driven
library:

```text
Router classification
  -> skills/manifest.json task_type_tags
  -> scripts/skill_index.py context
  -> route.skill_context
  -> Bot#1 / Tester / Bot#2 role-specific prompt package
```

The runtime contract is:

- load only the selected `SKILL.md` paths for the current worker role;
- do not load the full `skills/` tree into a prompt;
- expose DevOps/GitHub write skills as `gated_skills` until human approval;
- execute any skill script or external write only through `scripts/tool_gateway.py`.

This is the host-side integration point for the Telegram bot: Telegram tasks
should enter through `scripts/process_orchestrator.py run`, which always records
`route.skill_context` and a `skill_context_selected` process event.

## Change Routing Rules

Use this table before changing files:

| Goal | Change Location | Deploy Shape |
|---|---|---|
| Change agent personality or operating rules | `AGENTS.md`, `memories/`, `prompts/` | sync/mount into `/opt/data` or `/opt/hermes` |
| Add a reusable Hermes capability | `skills/<skill>/SKILL.md` and optional scripts | sync skill into `/opt/data/skills` |
| Add CRM/read-only business tool | `custom/tools/` | mount into container as read-only tool |
| Change LLM gateway, budget, fallback | `custom/yandex-proxy/` | rebuild/restart `hermes-yandex-proxy` |
| Change process gates, Bot#2 review, routing | `scripts/`, `configs/`, `docs/` in this repo | host-side patch plus tests |
| Change core agent loop or tool executor | `hermes-core/` | upstream-aware fork/submodule update only |
| Production deploy/restart | `docker-compose.yml` and server state | human gate, backup, focused smoke check |

## Architectural Decision

Do not move the current MVP into a new `src/` tree yet.

Reason:

- The live agent is already a mature upstream runtime.
- The Retek-specific code is acting as a supervisor, not as the main agent.
- A large internal restructure would not automatically affect the running
  Telegram agent because the container does not import these host scripts.
- Compatibility and operator clarity are more valuable right now than a cleaner
  Python package layout.

Recommended style:

- Keep host-side scripts small and CLI-friendly.
- Add new functionality as adapters around the live runtime.
- Prefer explicit contracts, tests, and dry-run modes over deep rewrites.
- Treat server writes as gated operations with backup and rollback notes.

## Safe Development Flow

1. Classify the requested change by the routing table above.
2. Make the smallest compatible change in the matching layer.
3. Add or update focused tests for code changes.
4. Run local tests.
5. Push to a feature branch.
6. For server changes, inspect the live tree first because it may contain local
   untracked files and owner-specific patches.
7. Apply server patches only with backup and focused smoke checks.

## Current Integration Risks

- The live `/opt/hermes-assistant` tree is not a clean clone. Avoid blind
  `git pull`, `git reset`, or full directory sync.
- Some host files are owned by different users (`root`, `hermes-bot`, `yc-user`).
  Server patches may require `sudo`, but this should stay explicit and audited.
- The Docker container receives only selected files through mounts. A repository
  commit is not automatically a runtime deployment.
- The server has a Git remote that embeds a token in the remote URL. That should
  be rotated or replaced with a safer credential strategy.

## Near-Term Roadmap

1. Document and automate a config/skills sync that targets `/opt/data`, not
   `hermes-core`.
2. Add a host-side `tool_gateway.py` only for dangerous external writes, leaving
   normal Hermes tool use inside the container.
3. Harden process state transitions around Bot#2 and human gates.
4. Add a deployment checklist that understands Docker mounts and dirty server
   state.
