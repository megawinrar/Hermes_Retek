# Refactoring Plan — Hermes_Retek

> Архитектурный план рефакторинга и покрытия тестами. Подготовлен в роли
> **Architect** (hermes-architect). Дата: 2026-06-18.

## Baseline (зафиксировано до начала работ)

- Тесты: **109 passed** за ~9 с (`.venv/bin/python -m pytest -q`).
- Покрытие строк: **~52%** (`coverage run --source=scripts -m pytest`).
- Прод-код: `scripts/*.py`, 19 модулей, ~8044 строки.
- Тесты импортируют модули через `sys.path.insert(0, scripts/)`; части suite-скриптов
  гоняются как subprocess (поэтому показывают 0% in-process, но имеют smoke-покрытие).

Эта зелёная база — наш эталон. **Инвариант рефакторинга: после каждого шага
`pytest` остаётся зелёным, поведение не меняется** (кроме явно согласованных фиксов багов).

## Принципы (в духе самого проекта)

Проект исповедует anti-rubber-stamp и acceptance-contract-before-start. Применяем те же
правила к собственному рефакторингу:

1. **Characterization tests первыми.** Перед изменением кода закрываем тестами
   security-критичные швы, которые сейчас не покрыты. Только потом рефакторим.
2. **Поведение-сохраняющий рефакторинг** отделён от **фиксов багов**. Багфиксы —
   отдельными коммитами с тестом, демонстрирующим старое vs новое поведение.
3. **Single-writer / малые шаги.** Каждый шаг — атомарный коммит, прогон тестов,
   только потом следующий.
4. **Evidence, не «согласие».** Приёмка шага = зелёные тесты + diff, а не «выглядит ок».

## Распределение ролей

| Роль | Скилл | Зона в этом рефакторинге |
|------|-------|--------------------------|
| **Architect** | hermes-architect | Этот план, границы модулей, порядок шагов, приёмка |
| **Tester** | hermes-tester | Characterization-тесты ДО рефакторинга; топ-15 недостающих тестов; регрессионный прогон |
| **Developer** | hermes-developer | Извлечение общих утилит, дедупликация, фиксы багов |
| **Bot#2 / Reviewer** | code-guardian | Ревью каждого diff: нет ли скрытого изменения поведения, заглушек, «тестов ради тестов» |
| **DevOps** | hermes-devops | Ветка/PR, прогон CI-эквивалента локально, gated до human approval |

## Фазы

### Phase 0 — Safety net (Tester) — БЛОКИРУЮЩАЯ
Добавить characterization-тесты на нынешнее поведение security-швов **до** любого
рефакторинга. См. `02_test_coverage.md` → топ-15. Минимум до рефактора:
- `tool_gateway.classify_command` / `gateway_decision` fail-closed,
- `bot2_gate.should_escalate`,
- `supervisor_common.parse_bot2_verdict` / `extract_bot2_verdict`,
- `supervisor_common.acquire_resource_locks` (конфликт + rollback),
- `secret_audit.scan_history` (private-key в истории),
- `task_router.classify_task` граничные случаи.

### Phase 1 — Извлечь общие утилиты (Developer)
Создать `scripts/_common.py` с: `utc_now`, `load_env`/`load_env_file`, `dumps`,
генератор id `f"{prefix}-{ts}-{uuid[:6]}"`, паттерн открытия SQLite (WAL +
`executescript` + `_INITIALIZED` guard). Заменить ~5 копий. Низкий риск, высокая отдача.

### Phase 2 — Единые источники истины (Developer + багфиксы)
- `scripts/levels.py`: `LEVELS`, `LEVEL_RANK`, `RISK_RANK` (сейчас дублируются в
  task_router и skill_index).
- `scripts/json_salvage.py`: единый экстрактор JSON из LLM-ответа (3 копии regex).
- **Багфикс** escalation-status drift и decision-vocabulary drift (см. `03_latent_bugs.md`).

### Phase 3 — Унифицировать Bot#2 verdict/repair (Developer)
Извлечь «invalid → repair → failed_closed» (4 копии) в один helper; централизовать
извлечение вердикта. Средний риск — прикрыто Phase 0 тестами.

### Phase 4 — Suite harness (Developer)
`scripts/suite_harness.py` для real_task_suite/stage2_battle_suite (60–70% дубль) и
единый markdown-report builder. Низкий риск — suite сами себя проверяют.

### Phase 5 — Декомпозиция god-функций (Developer)
Разбить `run_process`/`continue_process`, общий post-Bot#2 «хвост». Самый высокий риск —
делать последним, под полным зелёным набором.

## Приёмка (acceptance criteria)

- Все существующие 109 тестов зелёные после каждой фазы.
- Новые characterization-тесты добавлены и зелёные.
- Покрытие строк выросло (цель: 52% → ≥70%, security-швы → ≥90%).
- Latent-баги покрыты тестом «было/стало» (если фиксы согласованы).
- Ни одного изменения поведения вне списка согласованных багфиксов.

## Связанные документы
- `01_code_smells.md` — детальные находки по файлам.
- `02_test_coverage.md` — карта покрытия и топ-15 недостающих тестов.
- `03_latent_bugs.md` — найденные реальные баги.
