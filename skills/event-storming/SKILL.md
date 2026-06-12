---
name: event-storming
version: 1.0.0
description: "DDD Event Storming: discover domain events, aggregates, bounded contexts."
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [ddd, event-storming, domain-driven-design, microservices, bounded-contexts]
    related_skills: [architecture-diagram, adr, plan]
---

# Event Storming (DDD)

Collaborative modeling technique for complex business domains.

## Steps

1. **Collect Domain Events** (orange stickers)
   - Past tense verbs: "OrderPlaced", "PaymentReceived"
   - No ordering, no filtering — brainstorm all

2. **Establish Timeline** (chronological order)
   - Arrange events left-to-right
   - Identify parallel streams

3. **Add Commands** (blue stickers)
   - What triggers each event? "PlaceOrder" → "OrderPlaced"

4. **Identify Aggregates** (yellow circles)
   - Cluster events around entities: Order, User, Payment

5. **Draw Bounded Contexts** (solid lines)
   - Group aggregates by business capability
   - Each context = potential microservice

6. **Map Relationships**
   - Context maps: upstream/downstream, shared kernel, anticorruption layer

## Output
- `/home/hermes-bot/workspace/knowledge/event-storming/YYYY-MM-DD-domain.md`
- Include diagram reference to architecture-diagram skill
