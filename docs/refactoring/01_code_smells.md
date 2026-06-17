# Code Smells & Refactoring Findings (per file)

> Находки разведки по прод-коду `scripts/*.py`. Effort: S/M/L. Cite — `file:line`.

## Cross-cutting refactors (ranked by value / risk)

1. **Унифицировать «Bot#2 invalid → repair → failed_closed»** — дублируется **4×**:
   `process_orchestrator.py` (750–777 и 948–975), `bot2_gate.review_with_bot2_repair`
   (417–449), `dual_bot_repair_loop.repair_bot2_verdict` (109–134). Варианты уже
   разошлись по risk-тегам. **Value: High, Risk: M.**
2. **Единый источник статусов эскалации и словаря human-decision** — `bot2_gate`
   разошёлся с `supervisor_common` (см. `03_latent_bugs.md`). **Value: High, Risk: L.**
3. **Общие утилиты** — `utc_now` (во всех 5), `load_env`/`load_env_file` (идентичны в 2),
   `dumps`, генератор id, паттерн открытия SQLite (4 копии, одна без guard).
   В `scripts/_common.py`. **Value: High, Risk: L.**
4. **`rows_with_json` helper** — `dict(row) | {key: loads(row[col])}` + `pop(col)`
   повторяется ~10× (supervisor_common.task_details, process_orchestrator, dual_bot_lab,
   bot2_gate). **Value: Med, Risk: L.**
5. **Декомпозиция `run_process`/`continue_process`** + общий post-Bot#2 хвост (~90 строк
   verbatim). **Value: High, Risk: M.**
6. **`scripts/json_salvage.py`** — regex `(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})` дублируется в
   task_router:296–313, dual_bot_suite:82–97. Centralize. **Value: Med, Risk: L.**
7. **`scripts/levels.py`** — `LEVELS`/`LEVEL_RANK`/`RISK_RANK` дублируются в task_router
   (70, 192–193) и skill_index (122, 444, 452). **Value: Med, Risk: L.**
8. **Suite harness** — real_task_suite и stage2_battle_suite дублированы на 60–70%
   (args-namespace, assert_fields/value_at, process_case, gateway_case, argparse-скелет).
   **Value: High, Risk: M.**
9. **Единый markdown-report builder** — 4 кустарных генератора (real_task 232–260,
   stage2 294–334, dual_bot 149–191). **Value: Med, Risk: L.**
10. **Декларативные таблицы в `tool_gateway`** — `classify_command` (104–138) и
    `resources_for_risks` (141–152) должны быть одной таблицей risk→resource.
    **Value: Med, Risk: M (security-sensitive).**

## process_orchestrator.py (2249)
God-функции: `run_process` (1510–1777, ~267), `continue_process` (1295–1507, ~212,
хвост дублирует run_process), `live_dual_result` (629–839), `live_bot1_revision_result`
(842–1014), `process_summary` (1825–1961). Magic-статусы как сырые строки вместо
констант из supervisor_common. `SystemExit` как доменный «not found» по всему файлу.
Dry-run определяется по суффиксу session-id (`process_was_dry`, 1247–1250) — хрупко.

## supervisor_common.py (753)
Самый чистый файл, но смешивает 4 домена: DB/task-модель, verdict-парсинг (441–474),
subprocess-обёртки (648–716), resource-locks. Кандидат на разделение
`supervisor_db.py` / `verdict.py` / `subprocess_tools.py`. `task_details` (719–753) —
4 почти одинаковых блока (см. `rows_with_json`).

## dual_bot_lab.py (705)
`call_chat_payload` (226–272) — плотная лестница классификации ошибок. Промпт-билдеры
(275–541) — 6 функций с общим скелетом `[{system},{user}]` + boilerplate. Route-audit
JSON-схема инлайн (376–384) вместо константы. `db()` без `_INITIALIZED` guard (137).

## bot2_gate.py (637)
Дублирование с supervisor_common (escalation, decision, `load_env`, `dumps`, `utc_now`).
`send_telegram` (193–223) — curl+chunking+subprocess в одной функции. Хардкод
`DEFAULT_TELEGRAM_CHAT_ID="245167740"` (36), `--socks5 127.0.0.1:1080` (212), chunk 3500.

## dual_bot_repair_loop.py (505)
`run_case` (244–416, ~172) — LLM+печать+repair в одном глубоко-вложенном цикле, почти
дубль `live_dual_result`. Тонкие wrapper-функции (105, 137–156) без пользы. `CASES`
(24–73) — большой инлайн-литерал, кандидат в fixture.

## task_router.py (329)
`classify_task` (144–203) — нормализация + 13 regex + 9-веточная elif-лестница (порядок
load-bearing, недокументирован) + 4 пересчёта риска. Magic-наборы `{"L3","L4"}` и
deploy-слова дублируются. JSON-salvage (288–313) — см. cross-cutting #6.

## tool_gateway.py (351)
`classify_command` (104–138) — 11 ad-hoc if-правил с magic-именами; `" compose "`
паддинг хрупкий. Два параллельных switch (risk + resource) должны быть одной таблицей.
`latest_bot2_verdict`/`has_user_override_decision` (164–197) — почти идентичны.

## skill_index.py (469)
Кэширование пронизывает всё: 2 глобальных кэша с ручным TTL/mtime/deepcopy/LRU.
3 парсера env-var (25–42) → `_env_int`. `validate_manifest` (87–130) — длинный
императивный валидатор, magic-список required (101–112), инлайн `["L0".."L4"]` (122).
Нет типизированной модели Skill (везде `item.get(...)`).

## secret_audit.py (261) / secret_patterns.py (54)
`scan_history` (143–174) — process+parse+finding в одной функции. `git_grep_pattern`
для `private_key` (137–140) дублирует regex из secret_patterns.py:33 — два источника.
Фикс: поле `grep_pattern` на `SecretPattern`. secret_patterns.py чистый.

## real_task_suite.py (286) / stage2_battle_suite.py (360) / dual_bot_suite.py (246)
60–70% общего кода (см. cross-cutting #8). `del timeout` мёртвые параметры
(stage2:44,90). Мёртвое условие `show_failed` (real_task:114). `extract_verdict`
(dual_bot:81) — 3-й независимый verdict-парсер. dual_bot_suite — единственный модуль
вообще без тестов (только py_compile).
