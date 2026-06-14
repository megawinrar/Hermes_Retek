# Hermes Retek Process Architecture MVP

## Goal

Make the Bot#1/Bot#2 scheme useful for cleaner code by turning it into a gated
process, not a free-form conversation.

## Process Split

```text
User task
  -> Router
  -> Supervisor/state owner
  -> Architect when L3/L4
  -> Bot#1 implementer
  -> Tester/evidence collector
  -> Bot#2 reviewer
  -> Human Да/Нет if unresolved disagreement
  -> DevOps only after approval
```

## Workers

- `router`: classifies L0-L4, risk, model class, process plan.
- `supervisor`: owns task id, state, approval, human decision.
- `architect`: creates acceptance contract for L3/L4.
- `bot1`: DeepSeek/Bothub implementer.
- `tester`: runs checks and writes evidence.
- `bot2`: Codex/Bothub quality gate and conservative classification auditor.
- `devops`: deploys only after `approved` or `accepted_by_user_override`.

## Invariants

- Bot#1 and Bot#2 do not chat directly.
- Supervisor is the only process that changes approval state.
- Router is the first source of `L0`-`L4` classification.
- Bot#2 classification audit may only raise task level, raise risk, require
  review, or require a human gate. It cannot lower or relax Router policy.
- External writes are single-writer.
- Bot#2 is mandatory for code gates, multi-agent workflows, deploy, auth, data,
  DB, CI/CD, prompt/policy changes, and user-requested strict review.
- If Bot#2 returns `REJECT` or `NEEDS_HUMAN`, Supervisor must show Bot#1 version,
  Bot#2 version, risk, recommendation, and `Да/Нет` semantics.

## Complexity Levels

- `L0`: no-LLM commands/status.
- `L1`: short text tasks.
- `L2`: standard analysis/checklist.
- `L3`: architecture/strategy/multi-step task.
- `L4`: code/project/deploy task.

Adversarial prompts are not a new level. They are marked by
`stress_profile=adversarial`, risk is raised to `high`, and human gates become
more likely.

## Classification Audit

The default path is policy-first:

```text
Router deterministic classification
  -> optional Bot#2 classification audit
  -> Supervisor applies the higher-risk route
```

Bot#2 classification audit exists to catch under-classification. It can return
`recommended_level`, `risk_level`, `review_required`, and
`human_gate_required`, but Supervisor applies only stricter changes. Attempts to
lower `L4` to `L1`, change `high` risk to `low`, or remove a human gate are
recorded as ignored demotions.

## Acceptance Criteria

- Router returns deterministic JSON with `task_level`, `risk_level`,
  `process_plan`, and `review_required`.
- Bot#2 classification audit can raise route strictness and is auditable.
- L0 never starts Bot#1/Bot#2.
- Code/deploy/auth/data tasks route to L4/high-risk gates.
- Supervisor creates a process run and a linked Supervisor task.
- Bot#2 approval moves the task to `approved`.
- Bot#2 rejection/needs-human creates `awaiting_human_decision` and stores a
  human escalation.
- Human `Да` returns Bot#1 to fixes; `Нет` accepts Bot#1 by user override.
- DevOps is blocked until `approved` or `accepted_by_user_override`.
- Tests cover router levels, approval path, rejection path, and process audit
  trail.

## Battle Run

After tests pass, run examples through:

```bash
sudo scripts/process_orchestrator.py run --live-dual --task "..." --acceptance "..."
```

The battle examples should be supplied by the user so the first production-like
run reflects real work, not a synthetic demo.
