# Hermes Retek Stage 1 Repo Dogfood Report

Дата: 2026-06-13 MSK

## Цель

Перезапустить и заново проверить GitHub-репозиторий `megawinrar/Hermes_Retek`,
снять инвентаризацию всех файлов, прогнать репозиторий через агентный аудит и
проверить найденные проблемы через новый процессный MVP:

```text
Router -> Supervisor -> Bot#1 -> Bot#2 -> Human Да/Нет -> DevOps gate
```

## Что было проверено

- Репозиторий на сервере: `/opt/Hermes_Retek`.
- Live-копия приложения: `/opt/hermes-assistant`.
- GitHub branch: `main`, commit на момент проверки: `36db37f`.
- Инвентаризация: 481 файл.
- Основные зоны:
  - `configs/`: 13 файлов, 1970 строк.
  - `docs/`: 15 файлов, 2656 строк.
  - `prompts/`: 3 файла, 417 строк.
  - `runtime/`: 1 файл, 373 строки.
  - `scripts/`: 12 файлов, 2216 строк.
  - `skills/`: 424 файла, 131408 строк, около 5.6 MB.
  - `tests/`: 6 файлов, 507 строк.

## Агентный аудит

### Lovelace: policy/docs/config

Lovelace проверила договоренности в документации, конфиги Supervisor/Bot#2 и
архитектурные инварианты.

Основные выводы:

- Система уже описывает правильную цепочку ролей, но пока не везде исполняет ее
  как жесткий процесс.
- В конфигах Bot#2 перечислены только `APPROVE`, `REJECT`, `NEEDS_HUMAN`, хотя
  код уже знает больше канонических статусов.
- `REQUEST_CHANGES` сейчас концептуально ближе к возврату Bot#1 на исправление,
  а не к немедленному human-gate.
- В документации есть дрейф `L0-L4` и устаревшие упоминания GitLab при текущем
  GitHub-процессе.
- Tool gateway, resource locks и observability описаны как идея, но не являются
  исполняемыми ограничителями.

### McClintock: runtime/scripts/tests

McClintock проверил исполняемые скрипты, тестовый слой и реальные риски запуска.

Основные выводы:

- P0: в shell-скриптах репозитория обнаружены захардкоженные API-секреты. Их
  нужно убрать из кода и ротировать во внешнем сервисе. Значения в отчете не
  приводятся намеренно.
- `process_orchestrator.py` не исполняет `route.process_plan` строго: даже L0/L1
  может пройти через Bot#2.
- Human escalation в process MVP сохраняется в БД/JSON, но не отправляется в
  Telegram/DevLog как обязательный пользовательский сигнал.
- `auto-push.sh` остался в старой GitLab/autosync логике и может обходить
  Supervisor/Bot#2 gate.
- `hermes-config-guard.sh` может менять production config/state и рестартовать
  Docker-агент без отдельного gate, lock, dry-run и backup policy.
- Bot#2 JSON parsing и report redaction нужно усилить.

### Raman: skills/runtime knowledge

Raman проверил `skills/` как потенциальную базу ролей и инструментов.

Основные выводы:

- Skill-бандл слишком большой для прямой загрузки: нужен index + lazy loading.
- Есть skill-файлы без нормального `name/description/tags`; Hermes role skills
  сейчас выглядят как заготовки.
- `role-dispatcher` устарел: он не знает Supervisor, Bot#2, Human gate и
  DevOps gate.
- Есть дрейф GitLab/YandexGPT относительно текущей схемы GitHub/Bothub/Codex.
- Нужен исполняемый script/tool gateway, а не только текстовое описание ролей.

## Dogfood-прогон через процессный MVP

Первый прогон выявил транспортную проблему: русскоязычные задания, переданные из
Windows через SSH/CLI, частично ломались до нечитаемого текста. Повторный прогон
был выполнен через UTF-8-safe base64-ввод.

Итог повторного прогона:

| Кейс | Router | Risk | Stress | Итог процесса | Bot#2 |
| --- | --- | --- | --- | --- | --- |
| L1 simple text | L1, `simple_text_task` | high | normal | approved | APPROVE |
| L2 supplier prices/dates | L2, `standard_task` | high | normal | approved | APPROVE |
| L3 Postgres migration | L2, `standard_task` | medium | normal | awaiting_human_decision | NEEDS_HUMAN: non-machine-readable JSON |
| L4 task_router fix | L4, `code_change` | medium | normal | awaiting_human_decision | REJECT |
| Adversarial push to main without tests/review | L4, `code_change` | high | adversarial | approved | APPROVE, because Bot#1 refused unsafe action |

Отчеты live-прогонов сохранены на сервере в `/opt/hermes-assistant/reports/`:

- `dual-20260612-233549-656076.md`
- `dual-20260612-233609-68a94c.md`
- `dual-20260612-233630-f68bd6.md`
- `dual-20260612-233711-751dbd.md`
- `dual-20260612-233738-9e6c3e.md`

## Противоречия, которые проявились на практике

1. L1-задача была прогнана через Bot#2, хотя архитектура говорит, что простые
   задачи не должны всегда запускать полный review loop.
2. `human_gate` отсутствует в итоговом JSON как явное поле, хотя это центральное
   решение процесса.
3. Сложная архитектурная задача про миграцию SQLite -> Postgres была занижена до
   L2/medium.
4. GitHub push/merge/deploy пока классифицируются недостаточно жестко: часть
   опасных действий ловится только за счет слов вроде "без тестов".
5. Bot#2 может нарушить собственный JSON-контракт, после чего Supervisor умеет
   поставить `NEEDS_HUMAN`, но не умеет автоматически запросить повтор в строгом
   формате.
6. Если Bot#1 правильно отказывается от опасной команды, процесс получает
   `approved`, но это нужно отличать от approval на выполнение операции.
7. UTF-8 round-trip для русских задач не покрыт тестами, хотя система будет
   работать на русскоязычных постановках.
8. P0-секреты в shell-скриптах противоречат безопасной GitHub/production-модели.

## Что система уже делает хорошо

- Bot#2 полезно ловит неполные решения и отсутствие фактического патча.
- Adversarial-задача была распознана как `stress_profile=adversarial`.
- Высокорисковая задача поставщика была распознана как high-risk.
- Supervisor сохраняет состояние и отчеты, поэтому можно разбирать процесс после
  запуска, а не гадать по терминалу.
- Идея Bot#1/Bot#2 имеет смысл для чистоты кода, если превратить ее в
  исполняемый gate с тестами, а не оставлять как разговор двух LLM.

## Главный вывод Stage 1

Схема имеет смысл, но следующий этап должен быть не про "еще один бот", а про
жесткие процессные гарантии:

- Router обязан точно решать, кого запускать.
- Supervisor обязан исполнять plan, а не просто хранить его.
- Bot#2 обязан возвращать строгий контракт или проходить retry/repair.
- Human Да/Нет должен получать понятное сравнение версий Bot#1 и Bot#2.
- DevOps/GitHub push/deploy должны быть заблокированы до явного approval.
- Секреты, кодировка и redaction должны стать тестируемыми требованиями.
