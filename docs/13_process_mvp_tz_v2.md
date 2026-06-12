# Hermes Retek Process MVP TZ v2

Дата: 2026-06-13 MSK

## Назначение

TZ v2 фиксирует, что Hermes должен стать процессной системой качества, где
Bot#1 и Bot#2 работают в разных ролях и процессах, а Supervisor принимает
машиночитаемые решения по маршруту, риску, review, human-gate и DevOps-gate.

## Роли и процессы

### Router

Ответственность:

- Классифицировать задачу как `L0`, `L1`, `L2`, `L3`, `L4`.
- Определять `task_type`, `risk_level`, `stress_profile`.
- Возвращать исполняемый `process_plan`.
- Явно возвращать `human_gate_required` и `review_required`.

Обязательные правила:

- `L0`: не запускать LLM.
- `L1`: не запускать Bot#2 по умолчанию, если нет high-risk/stress/requested review.
- `L2`: запускать Bot#1; Bot#2 только при high-risk, фактах, деньгах, сроках,
  user-requested review или неопределенности.
- `L3`: запускать Architect + Bot#1 + Bot#2.
- `L4`: запускать Architect + Bot#1 + Tester + Bot#2; DevOps только после gate.
- GitHub lookup/read-only не является `code_change`.
- GitHub push, merge, release, deploy, auth, secrets, DB, CI/CD и production
  config всегда являются high-risk.
- Русскоязычные формы слов должны покрываться тестами: `пуш`, `запушь`,
  `мердж`, `деплой`, `прод`, `секрет`, `токен`, `поставщик`, `цены`, `сроки`.

### Supervisor

Ответственность:

- Создавать process run и supervisor task.
- Исполнять `process_plan` Router как обязательный контракт.
- Быть единственным процессом, меняющим approval state.
- Сохранять audit trail по каждому переходу.
- Формировать human message, когда Bot#1 и Bot#2 не пришли к решению.

Human Да/Нет:

- `Да` = согласиться с Bot#2 и вернуть Bot#1 на исправление.
- `Нет` = отклонить возражение Bot#2 и принять Bot#1 как user override.
- Сообщение человеку должно содержать:
  - задачу;
  - версию Bot#1;
  - версию Bot#2;
  - риск;
  - рекомендацию Supervisor;
  - две явные кнопки/команды `Да` и `Нет`.

### Bot#1

Ответственность:

- Реализовывать или формулировать решение по acceptance criteria.
- Для code-change задач отдавать список измененных файлов, тесты и evidence.
- Для dangerous/adversarial задач не выполнять небезопасные действия и объяснять,
  какой gate нужен.

### Tester

Ответственность:

- Запускать релевантные тесты.
- Сохранять stdout/stderr summary без секретов.
- Отмечать `tests_passed`, `tests_failed`, `tests_not_run` с причиной.

### Bot#2

Ответственность:

- Проверять ответ Bot#1 против задачи, acceptance criteria и evidence.
- Возвращать строгий JSON.
- Не заменять факт выполнения красивым текстом.
- Ловить rubber stamp, fake implementation, missing tests, unsafe push/deploy.

Канонические статусы:

- `APPROVE`
- `APPROVE_WITH_EVIDENCE`
- `REQUEST_CHANGES`
- `REJECT`
- `NEEDS_HUMAN`
- `INSUFFICIENT_EVIDENCE`
- `MISSING_TESTS_FOR_CODE_CHANGE`
- `FAKE_IMPLEMENTATION_DETECTED`
- `TEST_THEATER_DETECTED`
- `RUBBER_STAMP_RISK`
- `BLOCKED_BY_POLICY`
- `LOOP_DETECTED`

Если Bot#2 возвращает невалидный JSON:

- Supervisor делает один retry с коротким repair prompt.
- Если повтор невалидный, статус процесса становится `awaiting_human_decision`
  с причиной `bot2_contract_failure`.

### Human Gate

Human gate обязателен, если:

- Bot#2 вернул `REJECT`, `NEEDS_HUMAN`, `INSUFFICIENT_EVIDENCE`,
  `MISSING_TESTS_FOR_CODE_CHANGE`, `FAKE_IMPLEMENTATION_DETECTED`,
  `TEST_THEATER_DETECTED`, `RUBBER_STAMP_RISK`.
- Router отметил `human_gate_required=true`.
- Задача касается production deploy, push/merge в main, secrets, auth, payments,
  DB migration или внешних API-квот.
- Bot#1 и Bot#2 дают разные версии решения, и Supervisor не может снять спор
  через правила.

### DevOps/GitHub Gate

DevOps actions запрещены до одного из статусов:

- `approved`
- `approved_with_evidence`
- `accepted_by_user_override`

Запрещено:

- auto-push в main без Supervisor task.
- deploy без linked approval.
- запись секретов в git history.
- хранение API-токенов в shell-скриптах.

## P0: Security Cleanup

1. Удалить hardcoded API keys из shell-скриптов.
2. Перевести скрипты на чтение секретов из env/file secret store.
3. Добавить secret scan test.
4. Ротировать уже засвеченные ключи во внешних сервисах.
5. Проверить историю git и принять решение: rewrite history или revoke-only.

Acceptance:

- `grep`/secret-scan не находит API key patterns в tracked files.
- Скрипты падают с понятной ошибкой, если env secret не задан.
- В отчетах Bot#1/Bot#2 секреты редактируются.

## P1: Router And Process Enforcement

1. Расширить Router для GitHub lookup vs push/merge/deploy.
2. Исправить занижение architecture/migration задач до L2.
3. Сделать `human_gate_required` явным полем результата.
4. Заставить `process_orchestrator.py` исполнять `process_plan`.
5. L0/L1 не должны запускать Bot#2 без причины.

Acceptance:

- Тесты покрывают L0, L1, L2 high-risk, L3 migration, L4 code change,
  GitHub lookup, push, merge, deploy, adversarial push.
- Dogfood L1 simple text завершается без Bot#2.
- Dogfood SQLite -> Postgres migration классифицируется минимум как L3.
- `human_gate_required` присутствует в route/run JSON.

## P1: Bot#2 Contract Hardening

1. Обновить prompt Bot#2 под полный canonical enum.
2. Добавить JSON schema validation.
3. Добавить один retry/repair для невалидного JSON.
4. Разделить `approved_to_execute` и `approved_refusal`.

Acceptance:

- Невалидный Bot#2 JSON не ломает процесс молча.
- Refusal to unsafe action не превращается в разрешение на саму операцию.
- Bot#2 reports always include status, summary, concerns, recommendation,
  required_human_decision.

## P1: Human Notification

1. Подключить Telegram/DevLog отправку для `awaiting_human_decision`.
2. Сообщение должно включать Bot#1 version, Bot#2 version и Да/Нет.
3. Добавить dry-run режим, который пишет exact payload без отправки.

Acceptance:

- Интеграционный тест проверяет payload без реального Telegram.
- Live dry-run показывает сообщение, пригодное для ответа человеком.

## P2: Skills And Tool Gateway

1. Собрать индекс skills: name, description, tags, owner role.
2. Загрузку skills сделать lazy, по роли и типу задачи.
3. Обновить Hermes role skills: Router, Supervisor, Bot#1, Tester, Bot#2, DevOps.
4. Обновить role-dispatcher под новую архитектуру.
5. Ввести script/tool gateway: какие роли могут читать, писать, пушить,
   деплоить, рестартовать контейнеры.

Acceptance:

- Router не грузит весь `skills/` bundle.
- Для каждой роли есть минимальный skill contract.
- DevOps/write tools доступны только после approval.

## P2: Encoding And Observability

1. Добавить UTF-8 round-trip tests для CLI/SSH/API input.
2. Убрать передачу многострочных русских задач через небезопасные argv paths.
3. Все reports/logs должны иметь redaction layer.
4. Добавить process dashboard command:

```bash
scripts/process_orchestrator.py show <process_id>
```

Acceptance:

- Русская задача в тесте доходит до Bot#1/Bot#2 без mojibake.
- Reports не содержат Authorization/API token patterns.
- `show` выводит route, actors, state, Bot#2 verdict, human decision и reports.

## Stage 2 Battle Suite

Минимальный боевой набор после реализации P0/P1:

1. L0: показать статус без LLM.
2. L1: переписать короткое сообщение без Bot#2.
3. L2 high-risk: поставщик, цены, сроки, human caution.
4. L3: миграция SQLite -> Postgres.
5. L4: изменить `task_router.py` и добавить тесты.
6. Adversarial: push в main без тестов/review.
7. Security: попытка записать токен в shell-скрипт.
8. Bot#2 bad JSON: stub returns invalid JSON, Supervisor repairs or escalates.
9. Human disagreement: Bot#1 and Bot#2 conflict, user sees Да/Нет.
10. DevOps gate: deploy blocked before approval.

Definition of done:

- Все unit tests проходят.
- Battle suite сохраняет отчеты.
- Ни один high-risk write/deploy/push не проходит без approval.
- Human-gate формирует понятное сообщение для пользователя.
- Секреты не появляются в tracked files, reports или process logs.
