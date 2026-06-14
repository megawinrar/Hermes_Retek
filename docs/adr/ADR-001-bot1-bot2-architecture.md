# ADR-001: Bot#1/Bot#2 Gate Architecture with Supervisor

**Status:** Accepted (2026-06-14)
**Scope:** Hermes Retek pipeline

## Context

У Hermes Retek одна задача — отвечать пользователю в Telegram. Но задачи бывают разные: от `/status` до code change с тестами и деплоем. Раньше проверка качества была только через AGENTS.md и мою самодисциплину — что приводило к rubber-stamp приёмке, формальному закрытию задач и пропущенным ошибкам.

**Проблемы:**
- Нет независимой проверки кода перед approve
- Нет механизма human escalation при конфликте
- Нет классификации задач по сложности и риску
- Нет аудита принятых решений
- Нет быстрой навигации по прошлым сессиям

## Decision

Ввести трёхслойную архитектуру исполнения задач:

```
User Task
  │
  ▼
Router  →  L0-L4, risk, type, process_plan
  │
  ▼
Supervisor  →  task_id, статусы, решения, эскалации
  │
  ├── L0-L2: Router → Supervisor → Bot#1 (→ Tester → bot2_light)
  └── L3-L4: Router → Supervisor → Architect → Bot#1 → Tester → Bot#2 → Human(если REJECT) → DevOps
```

### 1. Router (task_router.py)

Классифицирует задачу по:
- **Уровень:** L0 (рутина) — L4 (code + deploy)
- **Тип:** code_change, standard_task, finance, tender
- **Риск:** low / medium / high
- **Process plan:** какие worker-ы включить

### 2. Supervisor (process_orchestrator.py)

Владелец lifecycle задачи:
- Создаёт task_id, acceptance contract
- Статусы: created → running → approved / awaiting_human_decision / failed
- Решение человека: Да (return_to_bot1) / Нет (accepted_by_user_override)
- Audit trail в SQLite

### 3. Bot#1 (DeepSeek V4 Flash)

Исполнитель. Пишет код, делает анализ. Прикладывает evidence. Не принимает результат.

### 4. Tester

Собирает test_commands, exit_codes, stdout.

### 5. Bot#2 (Codex/OpenAI)

Независимый проверяющий:
- Проверяет против ТЗ, а не против объяснения Bot#1
- Anti-rubber-stamp: approve только с evidence
- Mandatory dissent: 3+ слабых места для L3/L4
- Red-team question
- Вердикты: APPROVE / REQUEST_CHANGES / INSUFFICIENT_EVIDENCE / NEED_HUMAN

### 6. Human escalation

Bot#2 REJECT → awaiting_human_decision → Telegram сообщение
- Да = Bot#1 на доработку
- Нет = accept override (DevOps разблокирован)

### 7. Session Tags (расширение 2026-06-14)

После задачи Supervisor сохраняет теги (уровень, риск, домен, pipeline этапы).
Поиск через search_by_tags() — 1ms.

## Consequences

### + Положительные
- Независимая проверка кода
- Anti-rubber-stamp
- Human-in-the-loop
- Audit trail
- Сессии тегируются

### - Отрицательные
- Дороже по токенам (Bot#2 = Codex)
- Pipeline L4: ~5 мин вместо 30 сек
- 6+ worker-ов, сложнее поддержка
- SQLite stores без TTL

### Риски и митигации
- Rubber-stamp через привыкание → random audit Supervisor (L4: 50%)
- Рост SQLite → auto-prune > 30 дней
- Сбой Bot#2 → fallback bot2_light или human escalation

## Compliance

- AGENTS.md (Runtime-контракт): Bot#1, Tester, Bot#2 — role-specific skills
- AGENTS.md (Human-in-the-Loop Gates): deploy требует approve
- process_workers.yaml: L0-L4 definitions
- PROCESS_ARCHITECTURE.md: полная схема

## References

- scripts/process_orchestrator.py
- scripts/bot2_gate.py
- scripts/task_router.py
- scripts/session_tags.py
- scripts/supervisor_common.py
- configs/process_workers.yaml
- configs/supervisor_pipeline.yaml
- docs/BOT2_MVP.md
- docs/SUPERVISOR_MVP.md
- docs/PROCESS_ARCHITECTURE.md
- docs/07_universal_bot2_guard_modes.md
- docs/08_anti_rubber_stamp_acceptance_protocol.md
- prompts/universal_bot2_guard_prompt.md
