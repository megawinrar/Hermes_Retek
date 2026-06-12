---
name: role-dispatcher
version: 1.0.0
description: Master dispatcher: routes tasks to role-based agents
metadata:
  hermes:
    tags: [multi-agent, dispatcher, roles, kanban]
---

# Role Dispatcher

## Роли
| Role | Профиль | Зона ответственности |
|------|---------|---------------------|
| Architect | hermes-architect | ADR, C4, Event Storming |
| Developer | hermes-developer | Код, TDD, Review |
| Tester | hermes-tester | QA, Test cases, Regression |
| DevOps | hermes-devops | CI/CD, Docker, Monitoring |
| Analyst | hermes-analyst | Требования, Данные, Отчёты |

## Auto-Dispatch
| Ключевые слова | Роль |
| архитектура, ADR, C4, микросервис | Architect |
| код, фича, TDD, refactor | Developer |
| тест, баг, QA, edge case | Tester |
| deploy, CI/CD, docker, k8s | DevOps |
| анализ, Excel, PDF, KPI | Analyst |

## Workflow
Analyst -> Architect -> Developer -> Tester -> DevOps
