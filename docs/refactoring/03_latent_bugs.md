# Latent Bugs Found During Assessment

> Это **не** смеллы, а реальные расхождения в логике, найденные при разведке.
> Каждый требует решения: фиксить в рамках рефакторинга (с тестом «было/стало»)
> или вынести отдельно.

## BUG-1 — Escalation-status drift (severity: HIGH)

`bot2_gate.should_escalate` (`scripts/bot2_gate.py:322`) держит собственный список
статусов эскалации, который **разошёлся** с каноном
`supervisor_common.ESCALATION_STATUSES`.

- В `supervisor_common` есть `NEED_HUMAN_DECISION` и `REFACTORING_REQUIRED`.
- В `bot2_gate.should_escalate` их **нет**.

**Эффект:** в gate-пути вердикты `NEED_HUMAN_DECISION` / `REFACTORING_REQUIRED`
не приводят к эскалации человеку — работа может «проехать» мимо human-gate.

**Фикс:** заменить локальный список на импорт `ESCALATION_STATUSES` (single source).

## BUG-2 — Human-decision vocabulary drift (severity: MEDIUM)

`bot2_gate.cmd_decide` (`scripts/bot2_gate.py:570`) маппит Да/Нет в статусы
`user_agreed_with_bot2` / `user_accepted_bot1`, тогда как
`supervisor_common.record_human_decision` использует `return_to_bot1` /
`accepted_by_user_override`. Два разных словаря для одного и того же решения.

**Эффект:** состояние задачи после решения человека зависит от того, через какой
путь оно пришло; возможны статусы вне `ALLOWED_STATUS_TRANSITIONS`.

**Фикс:** свести к единому словарю состояния. Требует подтверждения целевых статусов
(семантический выбор), поэтому риск средний.

## BUG-3 — `dual_bot_lab.db()` без `_INITIALIZED` guard (severity: LOW)

`scripts/dual_bot_lab.py:137` пере-выполняет `executescript` при **каждом**
подключении, тогда как два других store используют guard. Перформанс-смелл +
несогласованность; не корректностная ошибка, но стоит выровнять.

## Сопутствующие подозрения (проверить тестом, не подтверждены как баги)

- `real_task_suite.process_case` проверяет `result.get("show_failed")`, но
  `process_run` такого ключа не выставляет — мёртвое условие (`real_task_suite.py:114`).
- `build_acceptance_contract`: проверка длины `len(tz) < 40` стоит последней и может
  понизить high-risk короткое ТЗ до "low" (`supervisor_common.py:201`).
- `stage2_battle_suite.process_case` early-return кладёт `"checks": result`, но
  рендер отчёта читает другие ключи — деталь ошибки теряется в таблице.

## BUG-4 — Короткий секрет: классифицируется, но не редактируется (severity: LOW)

`tool_gateway.SECRET_WRITE_PATTERN` ловит `token=...{8,}` (8+ символов) и помечает
команду как `secret_write` (опасная). Но `redact_text` (secret_patterns) редактирует
только значения `{20,}` (20+ символов). Итог: секрет длиной 8–19 символов **попадает
в эхо команды** (`classification["command"]`) без редакции, хотя команда признана
опасной. Зафиксировано тестом `test_command_field_is_redacted` (использует 20+ символов,
чтобы проверять именно редакцию). Найдено в Phase 0.

**Фикс (опционально):** согласовать нижний порог редакции с классификатором, либо
понизить порог `secret_assignment` до 8. Требует решения — может дать ложные срабатывания.
