---
name: c4-model
version: 1.0.0
description: "C4 Model: Context, Container, Component, Code diagrams for architecture."
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [architecture, c4-model, diagrams, uml, documentation]
    related_skills: [architecture-diagram, adr, event-storming]
---

# C4 Model

Four-level architecture visualization: Context → Container → Component → Code.

## Levels

### L1: System Context
- Who uses the system? (actors)
- What external systems integrate?
- Scope: entire system as a box

### L2: Containers
- Applications, databases, file systems, queues
- Technology choices visible
- Inter-process communication

### L3: Components
- Major structural building blocks inside each container
- Interfaces between components

### L4: Code (optional)
- UML class diagrams for complex parts
- Usually auto-generated from code

## Rules
- Each level zooms into ONE element from previous level
- Use architecture-diagram skill for SVG generation
- Save to `/home/hermes-bot/workspace/knowledge/c4/`
