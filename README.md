# Hermes_Retek

Проектная база для настройки Hermes Retek: экономное потребление токенов, маршрутизация задач L0-L4, управление памятью, агентами и второй бот-контролёр качества.

## Что добавлено

### 1. Аудит репозитория

`docs/00_repo_audit.md`

Фиксирует текущее состояние репозитория и список проверенных направлений поиска.

### 2. Сценарий Token Governor

`docs/01_token_governor_scenario.md`

Описывает логику:

```text
Пользователь -> Task Router -> Budget Guard -> Memory Selector -> Model Selector -> Execution -> Reviewer -> Answer -> Checkpoint
```

### 3. YAML-политика токенов

`configs/token_governor.yaml`

Машинно-читаемая политика уровней:

- L0 — без LLM;
- L1 — дешёвая модель, без памяти и агентов;
- L2 — средняя задача, ограниченная память;
- L3 — сложная задача, planner + worker + reviewer;
- L4 — проектная задача, несколько агентов и checkpoint.

Также содержит правила Code Guard Bot: лимит спора 3 раунда, анти-имитационный контроль, stage gates, realtime Telegram DevLog, статусы нарушений и Telegram escalation с кнопками `Да` / `Нет`.

### 4. Обязательный контракт качества кода

`docs/05_code_quality_and_test_contract.md`

`configs/code_quality_contract.yaml`

Фиксирует жёсткое правило: если есть изменение кода, всегда нужны тесты, запуск тестов, результат запуска, оценка рефакторинга и проверка Bot#2. Нет программных задач, которые не надо тестировать.

### 5. Политика включения Bot#2

`docs/06_bot2_activation_policy.md`

`configs/bot2_activation_policy.yaml`

Фиксирует матрицу: когда Bot#2 обязателен, когда достаточно light-review, а когда Bot#2 будет лишней нагрузкой. Также закрепляет модельную схему: Bot#1 = DeepSeek как implementer, Bot#2 = OpenAI/Codex как reviewer, quality gate и арбитр.

### 6. Универсальные режимы Bot#2

`docs/07_universal_bot2_guard_modes.md`

`configs/universal_bot2_guard_modes.yaml`

Расширяет Bot#2 за пределы кода. Bot#2 может работать как:

- Quality Gate — проверка готового результата;
- Analytical Validator — проверка фактов, расчётов, источников и допущений;
- Creative Challenger — усиление идей, стратегий, офферов и позиционирования.

### 7. Anti Rubber-Stamp Acceptance Protocol

`docs/08_anti_rubber_stamp_acceptance_protocol.md`

`configs/anti_rubber_stamp_acceptance_protocol.yaml`

Запрещает формальную взаимную приёмку Bot#1 и Bot#2. Вводит acceptance contract до старта задачи, evidence-based approve, mandatory dissent, red-team question и random audit.

### 8. Prompt универсального Bot#2

`prompts/universal_bot2_guard_prompt.md`

Промт для Bot#2 вне кода: quality gate, analytical validator и creative challenger. Запрещает approve без acceptance criteria и evidence.

### 9. Шаблон Acceptance Contract

`configs/acceptance_contract_template.yaml`

YAML-шаблон условий приёмки до старта задачи. Фиксирует task type, mode, expected result, acceptance criteria, rejection criteria, доказательства Bot#1 и проверки Bot#2.

### 10. Prompt для Task Router

`prompts/task_router_prompt.md`

Промт классификатора задач. Он должен возвращать JSON с уровнем задачи, лимитами, моделью, памятью и агентами.

### 11. Второй бот Code Guard

`docs/02_code_guard_bot_scenario.md`

Сценарий второго бота, который следит за кодом, спорит с исполнителем максимум 3 раунда и обращается к человеку, если согласия нет.

### 12. Анти-имитационные сценарии Code Guard

`docs/03_code_guard_anti_imitation_scenarios.md`

Подробно описывает, как Bot#2 ловит ситуации, когда Bot#1:

- не написал реальный код;
- добавил заглушку;
- сделал тест ради теста;
- изменил только тесты, но не исправил поведение;
- утверждает, что задача выполнена, без доказательств в diff.

### 13. Realtime stage gates и Human-in-the-loop

`docs/04_realtime_human_in_loop_and_stage_gates.md`

Описывает гибридную схему наблюдения:

- GitHub — источник истины: branch, commits, PR, diff, CI, review;
- Telegram DevLog — live-окно: события, споры Bot#1/Bot#2, вопросы пользователю и кнопки решений.

### 14. Prompt для Code Guardian

`prompts/code_guardian_prompt.md`

Промт для агента-проверяющего: diff review, контроль архитектуры, тестов, рефакторинга, расхода токенов и формального выполнения задачи.

## Следующая реализация в коде

Рекомендуемые модули:

```text
src/router/task_router.py
src/router/token_budget.py
src/router/memory_selector.py
src/router/model_selector.py
src/agents/supervisor.py
src/agents/reviewer.py
src/telemetry/usage_logger.py
src/code_guard/pr_watcher.py
src/code_guard/diff_reader.py
src/code_guard/review_agent.py
src/code_guard/debate_manager.py
src/code_guard/human_escalation.py
src/code_guard/telegram_notifier.py
src/stage_gates/gate_manager.py
src/stage_gates/policy.py
src/stage_gates/event_schema.py
src/stage_gates/human_decision.py
src/realtime/telegram_devlog.py
src/realtime/event_bus.py
src/realtime/github_audit_log.py
```

## Главные правила

1. Сначала дешёвый Task Router.
2. Не грузить всю память по умолчанию.
3. Не включать агентов для L1/L2 без причины.
4. Сильную модель использовать только для planner/reviewer в L3/L4.
5. После превышения лимита делать checkpoint.
6. Code Guard спорит максимум 3 раунда, затем спрашивает пользователя.
7. Тест ради теста не считается выполнением задачи.
8. Bot#2 принимает только проверяемые доказательства: diff, тесты, результат запуска и соответствие ТЗ.
9. GitHub хранит доказательства, Telegram показывает процесс в реальном времени.
10. На L3/L4 перед важными решениями система спрашивает пользователя.
11. Любое изменение кода требует тестов, запуска тестов и оценки рефакторинга.
12. PR с изменением кода нельзя мержить без одобрения Bot#2 по тестам и рефакторингу.
13. Bot#2 не нужен для каждого сообщения Bot#1, но обязателен для каждого code gate.
14. DeepSeek пишет и исправляет, OpenAI/Codex проверяет и принимает.
15. Вне кода Bot#2 работает как аналитик, критик и креативный challenger на рискованных или важных задачах.
16. Согласие Bot#1 и Bot#2 не является доказательством; approve возможен только по acceptance criteria и evidence.
17. Для важных задач acceptance contract создаётся до выполнения и не меняется Bot#1/Bot#2 без разрешения.
