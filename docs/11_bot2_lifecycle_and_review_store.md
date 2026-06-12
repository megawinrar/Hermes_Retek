# Bot#2 Lifecycle and Review Store

## Зачем нужен отдельный lifecycle

Bot#2 не должен быть просто дополнительным prompt в конце задачи.

Он должен быть управляемым процессом:

- создаётся Supervisor на конкретном gate;
- получает структурированный context package;
- возвращает машинно-читаемый verdict;
- сохраняет review session и rounds;
- передаёт замечания Bot#1 через Supervisor;
- после лимита раундов эскалирует пользователю;
- оставляет audit trail для будущих проверок.

Без этого Bot#2 будет нестабилен:

- непонятно, почему он approve/reject;
- невозможно восстановить спор Bot#1/Bot#2;
- пользовательское решение `Да/Нет` не будет связано с конкретной проверкой;
- Bot#2 может повторно проверять то же самое и тратить токены;
- acceptance criteria и evidence могут потеряться между раундами.

## Главный принцип

Bot#2 общается с Bot#1 только через Supervisor.

Прямой свободный чат Bot#1 ↔ Bot#2 запрещён.

```text
Bot#1 -> Supervisor -> Bot#2 -> Supervisor -> Bot#1
```

Supervisor является владельцем state, approval и final decision routing.

## Когда создаётся Bot#2 review session

Bot#2 session создаётся только на gate:

- `plan_ready`;
- `before_execution_if_high_risk`;
- `verification_result`;
- `pre_merge_decision`;
- `human_escalation_request`;
- `code_guard_required`;
- `money_or_tender_risk`;
- `memory_rule_change`;
- `external_message_with_price_or_terms`.

Bot#2 не создаётся после каждого сообщения агента.

## Bot#2 Context Package

Bot#2 получает не весь хаотичный контекст, а короткий структурированный пакет.

Обязательные поля:

```yaml
bot2_context_package:
  task_id: ""
  review_session_id: ""
  phase: "plan_ready|verification|pre_merge|human_escalation"
  review_mode: "quality_gate|analytical_validator|creative_challenger|code_guard|document_guard|finance_guard|tender_guard|memory_guard|agent_guard"
  original_user_request: ""
  task_level: "L1|L2|L3|L4"
  task_type: ""
  acceptance_contract_ref: ""
  acceptance_criteria: []
  rejection_criteria: []
  bot1_claim: ""
  bot1_result_summary: ""
  evidence_refs: []
  changed_files: []
  tool_call_summary: []
  known_risks: []
  previous_rounds_summary: []
  question_for_bot2: ""
```

## Что Bot#2 обязан проверить

Bot#2 проверяет:

- исходное ТЗ;
- acceptance contract;
- evidence;
- результат Bot#1;
- пропущенные пункты;
- риск формального approve;
- конфликт с памятью проекта;
- необходимость спросить пользователя.

Bot#2 не проверяет убедительность объяснения Bot#1. Он проверяет доказательства.

## Review statuses

Bot#2 возвращает один статус:

- `APPROVE_WITH_EVIDENCE`;
- `REQUEST_CHANGES`;
- `INSUFFICIENT_EVIDENCE`;
- `RUBBER_STAMP_RISK`;
- `NEED_HUMAN_DECISION`;
- `BLOCKED_BY_POLICY`;
- `LOOP_DETECTED`.

## Формат verdict

```json
{
  "review_session_id": "bot2_review_...",
  "task_id": "task_...",
  "round_number": 1,
  "phase": "verification_result",
  "mode": "analytical_validator",
  "status": "REQUEST_CHANGES",
  "summary": "Краткий итог проверки",
  "checked_acceptance_criteria": [],
  "missing_acceptance_criteria": [],
  "evidence_checked": [],
  "missing_evidence": [],
  "risks_found": [],
  "must_fix": [],
  "red_team_answer": "Что должно быть правдой, чтобы Bot#1 ошибся",
  "what_was_checked": [],
  "what_was_not_checked": [],
  "residual_risk": "low|medium|high",
  "confidence": 0.0,
  "human_question": null
}
```

## Раунды Bot#1 / Bot#2

Если Bot#2 вернул `REQUEST_CHANGES`, Supervisor передаёт Bot#1 только структурированный список `must_fix`.

Bot#1 обязан вернуть:

```json
{
  "task_id": "task_...",
  "review_session_id": "bot2_review_...",
  "round_number": 2,
  "fixed_items": [],
  "not_fixed_items": [],
  "new_evidence_refs": [],
  "notes": ""
}
```

После этого Supervisor создаёт новый review round для Bot#2.

Максимум: 3 раунда.

После 3 раундов без консенсуса — human escalation.

## Human escalation

Если нужен пользователь, Supervisor создаёт сообщение:

```text
Сообщение от Bot#2

Режим: ...
Задача: ...

Что сделал Bot#1:
- ...

Что нашёл Bot#2:
- ...

Чего не хватает:
- ...

Риск:
- ...

Да — согласен с Bot#2, вернуть на доработку.
Нет — принять результат Bot#1 как есть.
```

Решение пользователя сохраняется в `human_decisions` и связывается с `review_session_id`.

## Review Store

Bot#2 не требует отдельную физическую базу данных.

Рекомендуется отдельная schema/tables внутри общей Postgres Hermes.

Минимальный набор таблиц:

```text
tasks
acceptance_contracts
evidence_items
tool_calls
bot2_review_sessions
bot2_review_rounds
bot2_verdicts
bot2_escalations
human_decisions
review_memory_items
```

Redis Streams используется для realtime событий.

Postgres используется для аудита, истории, evidence и обучения качества.

## Таблицы

### bot2_review_sessions

Хранит одну проверку Bot#2 на gate.

Поля:

- `id`;
- `task_id`;
- `phase`;
- `mode`;
- `status`;
- `created_at`;
- `closed_at`;
- `created_by`;
- `gate_name`;
- `acceptance_contract_id`.

### bot2_review_rounds

Хранит раунды спора Bot#1/Bot#2.

Поля:

- `id`;
- `review_session_id`;
- `round_number`;
- `bot1_claim_json`;
- `bot2_verdict_json`;
- `status`;
- `created_at`.

### bot2_verdicts

Хранит итоговый verdict каждого раунда.

Поля:

- `id`;
- `review_session_id`;
- `round_id`;
- `status`;
- `risks_json`;
- `missing_evidence_json`;
- `must_fix_json`;
- `residual_risk`;
- `confidence`;
- `red_team_answer`;
- `created_at`.

### bot2_escalations

Хранит эскалации пользователю.

Поля:

- `id`;
- `task_id`;
- `review_session_id`;
- `reason`;
- `telegram_message_id`;
- `buttons_json`;
- `status`;
- `created_at`.

### human_decisions

Хранит решения пользователя.

Поля:

- `id`;
- `task_id`;
- `review_session_id`;
- `decision`;
- `decision_text`;
- `decided_by`;
- `created_at`.

### review_memory_items

Хранит паттерны качества.

Примеры:

- Bot#1 часто повторяет discovery после approval;
- Bot#1 делает test theater;
- Bot#2 часто approve без evidence;
- пользователь отменил решение Bot#2;
- конкретный тип задачи требует дополнительной проверки.

## Review memory

Bot#2 не нужна отдельная пользовательская память.

Ему нужна review memory:

- типовые ошибки Bot#1;
- частые слабые evidence;
- false approve;
- false reject;
- пользовательские решения по спорным случаям;
- риски по типам задач.

Эта память используется только как подсказка для проверки, но не как доказательство.

## Интеграция с Loop Guard

Bot#2 review session не должна запускать новый discovery, если task уже в execution или verification.

Bot#2 может запросить дополнительный evidence, но только через Supervisor.

Supervisor решает:

- можно ли сделать новый tool call;
- есть ли evidence в cache;
- не будет ли loop;
- нужно ли спрашивать пользователя.

## Интеграция с Parallel Agents

Parallel agents собирают evidence.

Supervisor формирует context package.

Bot#2 проверяет gate.

Bot#2 не управляет агентами напрямую.

## Итоговое правило

Bot#2 — это не чат-участник.

Bot#2 — это управляемая review session с базой решений, evidence, rounds и escalation.

Без Review Store система не сможет объяснить, почему результат был принят или отклонён.
