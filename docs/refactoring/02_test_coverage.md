# Test Coverage Assessment

> Baseline: **109 passed**, line coverage **~52%** (`coverage run --source=scripts -m pytest`).
> Caveat: suite/CLI-модули показывают 0% in-process, т.к. гоняются как subprocess —
> у них есть smoke-покрытие, но нет branch-level.

## Module → coverage

| Module | Cov | Verdict |
|---|---|---|
| secret_patterns.py | 96% | Good (redact-функции прямо не тестируются) |
| task_router.py | 88% | Good |
| human_notification.py | 86% | Good |
| supervisor_common.py | 83% | Good core; gaps в verdict-parse и locks |
| skill_index.py | 75% | validate_manifest не тестируется |
| patch_telegram_supervisor_buttons.py | 67% | error-якоря + main не тестируются |
| process_orchestrator.py | 65% | много веток не покрыто |
| bot2_gate.py | 58% | **should_escalate не тестируется** |
| dual_bot_repair_loop.py | 58% | limit/stop ветки не покрыты |
| dual_bot_lab.py | 45% | call_chat_payload (сеть) 0% |
| secret_audit.py | 36% | **большинство scan-веток не покрыто** |
| devlog.py | 31% | telegram send body не исполняется |
| tool_gateway.py | 0%* | **нет unit-покрытия danger-классификатора** |
| dual_bot_suite.py | 0% | **тестов нет вообще** |
| real_task_suite / stage2_battle / supervisor_* | 0%* | только subprocess smoke |

`*` = покрыт через subprocess, in-process читается как 0%.

## Top-15 недостающих тестов (ранжировано)

| # | Функция · файл | Сценарий | Зачем |
|---|---|---|---|
| 1 | `should_escalate` · bot2_gate.py:322 | параметризовать все статусы вердикта | граница human-escalation; одна регрессия пропускает плохую работу |
| 2 | `gateway_decision` fail-closed + danger-детекторы · tool_gateway.py:104,282 | опасная cmd + сбой add_event; secret-write/kubectl/compose | ядро fail-closed безопасности, 0% unit |
| 3 | `parse_bot2_verdict`/`extract_bot2_verdict` · supervisor_common.py:447,464 | невалидный JSON, не-объект, неизв. статус, fenced | trust-boundary LLM→state machine |
| 4 | `scan_history` private-key · secret_audit.py:137 | подложить ключ в прошлый коммит | секрет в истории не должен становиться невидимым |
| 5 | `classify_task` границы · task_router.py:144 | 80-char L0 cutoff, code_change vs project, fallback | мисроутинг = недо-гейтинг |
| 6 | `acquire_resource_locks` конфликт/rollback · supervisor_common.py:586 | две задачи на пересекающиеся локи | concurrency, защита от одновременных деплоев |
| 7 | `run_case` max-rounds/non-REQUEST_CHANGES · dual_bot_repair_loop.py:386 | REQUEST_CHANGES каждый раунд → стоп на лимите | завершаемость retry-цикла |
| 8 | `call_chat_payload` fallback · dual_bot_lab.py:226 | HTTPError→fallback, timeout→RuntimeError | сетевая устойчивость всех lab/suite |
| 9 | `validate_manifest` error-ветки · skill_index.py:87 | dup name, missing field, high-risk без gateway | high-risk skill не должен грузиться без gateway |
| 10 | `redact_text`/`redact_payload` · secret_patterns.py:38,45 | каждый паттерн → [REDACTED], вложенность | редакция всех human-gate сообщений |
| 11 | `live_dual_result` happy-path APPROVE round1 · process_orchestrator.py:629 | Bot#2 APPROVE сразу | самый частый выход не проверен |
| 12 | `run_process` human_gate override · process_orchestrator.py:1657 | human_gate_required + Bot#2 approved → awaiting_human | гейт деплоя при model-approval |
| 13 | `extract_verdict` · dual_bot_suite.py:81 | fenced/bare/malformed → UNPARSEABLE | 2-й парсер в 0%-модуле |
| 14 | `build_acceptance_contract` low-risk · supervisor_common.py:201 | короткое ТЗ с high-risk словом | латентный баг длины |
| 15 | `assert_fields` + in-process process_case · stage2_battle_suite.py:96 | unit dotted-path + adversarial кейс | из subprocess-«непрозрачно» в реальное покрытие |

## Качество тестов
- `test_live_llm_prompt_quality.py` — не делает сетевых вызовов (имя вводит в заблуждение).
- `test_process_config::test_scripts_compile` — только `py_compile`.
- real_task/stage2/supervisor_smoke — subprocess-smoke: не локализуют падение.
- send-слой (`devlog.send_telegram_message`, curl) везде monkeypatched — тело не исполняется.
- Нет `conftest.py`/общих фикстур; каждый файл переинициализирует sys.path и свои хелперы.
- SQLite — temp-файлы (не in-memory); `_INITIALIZED_*` — мутабельный process-global по пути.

## Цель покрытия после работ
52% → **≥70%**, security-швы (tool_gateway, should_escalate, verdict-parse, secret_audit,
locks) → **≥90%**.
