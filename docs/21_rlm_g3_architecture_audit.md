# RLM + g3 Architecture Audit

Date: 2026-06-15

Sources reviewed:

- `alexzhang13/rlm` at `156fd725411b9cae822f5920a6cbf102a5473baa`
- `dhanji/g3` at `0ddb052d2b1f2c2113cafa6661974c8bb0f6996f`

## Decision

Hermes should implement an RLM-first memory/runtime database, then optionally
add RAG indexing over it.

RLM is the source-of-truth layer:

- runs;
- events;
- context items;
- compacted summaries;
- subcall links;
- artifacts;
- Bot1/Bot2 verdicts;
- test evidence;
- human decisions.

RAG is an optional retrieval accelerator later. It must not become the primary
state owner because vector search cannot reliably preserve exact decisions,
versions, approvals, or rollback evidence.

## What To Borrow From RLM

Copy the control model, not the local `exec()` implementation:

- bounded iterative loop;
- explicit final-answer readiness;
- context as addressable objects;
- versioned history and compactions;
- recursive subcall records with parent/child metadata;
- budget propagation to child calls;
- full trace stored outside active prompt;
- compact context packs for the next model call.

Hermes mapping:

```text
RLM context_0/history_0      -> SQLite context/history rows
RLM compaction summary       -> rlm record kind=compaction + context pack entry
RLM subcall                  -> child process/agent record with parent id
RLM final answer readiness   -> structured final_answer process event
RLM verbose trace            -> artifact/log row, not prompt stuffing
```

Avoid:

- in-process REPL `exec()` as a production isolation boundary;
- live Python variables as durable memory;
- regex-only action parsing for high-risk control flow;
- putting raw tool stdout into future prompts;
- giving child calls fresh unlimited budgets.

## What To Borrow From g3

Copy Studio's lifecycle shape, not direct merge semantics:

- session id;
- role/agent;
- status;
- workspace path;
- metadata;
- list/status/accept/discard workflow.

Hermes mapping:

```text
g3 Studio session         -> Hermes agent workspace/session record
g3 worktree              -> /opt/data/agent_workspaces/{process_id}/{agent_id}
g3 coach/player          -> Supervisor/Bot1
g3 reviewer/tester roles -> Bot2/Tester
g3 Huffman memory role   -> Context Engineer
```

Avoid:

- agents self-committing or self-merging;
- `accept` that directly merges to a hard-coded branch;
- Bot2 becoming the controller;
- context thinning into ungoverned temp files;
- discard/cleanup without Supervisor state checks.

## Current Implementation Slice

Implemented now:

- `scripts/rlm_store.py`
  - SQLite RLM-lite record store;
  - redaction before storage;
  - tag/process/kind search;
  - token-budgeted context packs;
  - JSON CLI.
- `scripts/context_budget.py`
  - 30/50/70/80 context pressure stages.
- `scripts/agent_workspace.py`
  - safe isolated workspace paths;
  - create/cleanup lifecycle foundation.
- `scripts/secret_vault.py`
  - secret refs and protected local file storage.
- `scripts/agent_roles.py`
  - machine-readable role contract.
- `scripts/process_orchestrator.py`
  - optional RLM sidecar writes via `--rlm-store`, `--rlm-enabled`, or
    `HERMES_RLM_ENABLED=1`;
  - process summary, Bot1 output, Bot2 review, human-gate, and browser-skill
    records;
  - `rlm_records_written` / `rlm_write_failed` process events.
- `skills/hermes-browser/SKILL.md` and `scripts/hermes_browser_session.py`
  - authenticated browser session skill with persistent profile, artifacts,
    redacted audit log, screenshots, HTML source capture, and cookie export.

This is intentionally not a full recursive execution engine yet. The first goal
is durable memory and bounded orchestration primitives with tests.

## Next Implementation Order

1. Add compaction records:

```text
kind=compaction
tags=context,compaction,{process_id}
metadata={source_event_ids, trigger_percent, token_budget}
```

2. Add subcall records for parallel agents:

```text
kind=subcall
metadata={parent_process_id, child_agent_id, depth, timeout, token_budget}
```

5. Add Supervisor-only accept/discard semantics for agent workspace outputs.
6. Add optional FTS5/vector index only after the durable SQLite RLM records are
   stable.

## Tests Required

Already covered:

- role contract;
- context thresholds;
- secret vault permissions/redaction;
- workspace path safety;
- RLM record add/search/context-pack redaction.

Next tests:

- workspace list/status lifecycle;
- process event to RLM record mirroring;
- compaction record generation at context thresholds;
- child-agent subcall linkage;
- Supervisor cannot accept workspace output without Bot2/human gate;
- restart can rebuild a compact context pack from SQLite without duplicating
  completed work.
