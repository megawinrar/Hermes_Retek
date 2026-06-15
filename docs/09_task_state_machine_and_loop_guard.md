# Task State Machine and Loop Guard

## Проблема

Hermes может начать повторять одни и те же действия по кругу:

- заново изучать тарифы;
- заново открывать одни и те же страницы;
- заново делать `cronjob list`;
- заново читать один и тот же config;
- снова переходить в discovery после согласия пользователя;
- смешивать cronjob-ответы с ручной задачей.

Это не проблема модели сама по себе. Это проблема отсутствия жёсткой машины состояний задачи.

## Главный принцип

После согласия пользователя на внедрение задача должна перейти из `discovery` в `execution`.

Возврат в `discovery` запрещён, если нет новой причины:

- verification failed;
- изменились входные данные;
- пользователь явно попросил перепроверить;
- tool result contradicts previous evidence.

## Task phases

### 1. intake

Задача принята от пользователя.

Разрешено:

- понять запрос;
- присвоить `task_id`;
- определить уровень L0-L4;
- определить тип задачи.

### 2. discovery

Сбор фактов.

Разрешено:

- смотреть конфиги;
- читать cronjob list;
- проверять тарифы;
- проверять сервисы;
- собирать evidence.

Запрещено:

- повторять один и тот же tool call с теми же args больше лимита;
- делать внедрение без плана.

### 3. plan_ready

План готов.

Bot обязан показать:

- что найдено;
- что будет изменено;
- какие риски;
- какие шаги выполнения;
- что требует согласия.

### 4. waiting_user_approval

Ожидание решения пользователя.

Разрешено:

- ждать ответ;
- уточнить только критически важный вопрос.

Запрещено:

- продолжать discovery без запроса пользователя;
- выполнять новые внешние действия.

### 5. execution

Пользователь дал согласие.

Разрешено:

- делать backup;
- менять конфиг;
- обновлять cronjob;
- писать файлы;
- перезапускать сервис;
- запускать smoke tests.

Запрещено:

- снова начинать pricing research;
- снова делать discovery без причины;
- повторять `cronjob list`, если данные уже есть и не изменились;
- смешивать cronjob task context с manual task context.

### 6. verification

Проверка результата.

Разрешено:

- проверить config diff;
- проверить статус сервиса;
- проверить cronjob output;
- проверить smoke test;
- проверить fallback.

Если verification failed, можно перейти в `fixing` или `rollback`, но не в полный `discovery` без причины.

### 7. final_report

Итоговый отчёт пользователю.

Обязательные поля:

- что изменено;
- где изменено;
- какие тесты прошли;
- что не удалось;
- что требует решения пользователя;
- остаточный риск.

## Approval semantics

`Approved permanently` не считается бизнес-согласием на план.

Это только разрешение на использование инструмента.

Для перехода в execution нужен отдельный флаг:

- `user_approved_plan: true`
- `approved_plan_id: ...`
- `execution_checklist_created: true`

## Execution checklist

После согласия пользователя Hermes обязан создать checklist и идти по нему.

Пример:

```text
1. Сделать backup config.
2. Обновить api-limits-check.
3. Добавить fallback provider.
4. Перезапустить или reload service.
5. Запустить smoke test.
6. Проверить отчёт в Telegram.
7. Отправить final report.
```

Production note: `api-limits-check` is executed from inside `hermes-agent`.
Budget endpoints must use `http://hermes-yandex-proxy:8000`, not
`localhost:8001`/`127.0.0.1:8001`, which are host-only addresses.

Без checklist нельзя переходить в execution.

## Tool call deduplication

Hermes должен хранить fingerprint каждого tool call:

```text
tool_name + normalized_args + task_id + phase
```

Если одинаковый tool call повторяется:

- 1-й раз: разрешить;
- 2-й раз: разрешить только с причиной;
- 3-й раз: остановить и создать `LOOP_DETECTED`.

## Evidence cache

В рамках одного task_id нужно кешировать:

- checked pricing pages;
- checked config paths;
- cronjob list result;
- service status;
- provider status;
- test request result;
- discovered API limits;
- discovered costs.

Если evidence уже есть, Bot должен ссылаться на него, а не собирать заново.

## Cron/manual isolation

Cronjob task и ручная задача не должны смешивать context.

Каждый cronjob должен иметь:

- `task_type: cronjob`;
- свой `task_id`;
- свой context;
- свой output channel;
- запрет менять состояние active manual task.

Manual task должен иметь:

- `task_type: manual`;
- свой `task_id`;
- свой approval state;
- свой execution checklist.

## Loop detection triggers

Считать loop, если:

- одна и та же phase повторяется 2+ раза без новой evidence;
- один и тот же browser URL открыт 2+ раза в task_id;
- один и тот же ssh command повторён 2+ раза без причины;
- после user approval снова начался discovery;
- cronjob output изменил manual task phase;
- Bot повторил уже сделанный отчёт вместо выполнения.

## Required stop behavior

Если loop обнаружен, Hermes должен остановиться и написать:

```text
LOOP_DETECTED

Я повторяю уже выполненный этап.

Повторяемая фаза: ...
Повторяемое действие: ...
Уже собранные evidence: ...
Следующий правильный шаг: ...

Нужна команда пользователя или исправление state machine.
```

## Правильное поведение после согласия

Если пользователь сказал `да запускай`, Hermes должен:

1. зафиксировать `user_approved_plan: true`;
2. запретить возврат в discovery;
3. создать execution checklist;
4. выполнять checklist;
5. проверять результат;
6. отправить final report.

## Итоговое правило

Research нельзя повторять после approval.

Approval должен переводить задачу в execution.

Execution должен идти по checklist.

Любой повтор discovery после approval без новой причины — это loop bug.
