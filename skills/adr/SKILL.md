---
name: adr
version: 1.0.0
description: "Architecture Decision Records: document decisions with context, consequences, trade-offs."
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [architecture, adr, decisions, documentation, rfc]
    related_skills: [architecture-diagram, plan, spike]
---

# Architecture Decision Records (ADR)

Document every significant architectural decision with context and consequences.

## When to Use

- Choosing between frameworks/libraries
- Database schema design
- API design (REST vs GraphQL vs gRPC)
- Microservice boundaries
- Authentication/authorization approach
- Infrastructure choices (cloud, containers, serverless)

## ADR Template

```markdown
# ADR-NNN: [Short Title]

## Status
- Proposed / Accepted / Deprecated / Superseded by ADR-XXX

## Context
[What problem are we solving? What forces are at play?]

## Decision
[What we decided to do]

## Consequences
### Positive
- ...
### Negative
- ...
### Neutral
- ...

## Alternatives Considered
| Option | Pros | Cons |
|--------|------|------|
| A | ... | ... |
| B | ... | ... |

## Compliance
[How to verify this decision is followed]
```

## Storage
Save to `/home/hermes-bot/workspace/knowledge/adrs/ADR-NNN-title.md`
Index file: `/home/hermes-bot/workspace/knowledge/adrs/README.md`
