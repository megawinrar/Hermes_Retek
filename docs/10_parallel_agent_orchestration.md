# Parallel Agent Orchestration — безопасная параллельность агентов

## Цель

Ускорить Hermes за счёт параллельных агентов, но не сломать уже введённую логику:

- Task State Machine;
- Loop Guard;
- Bot#2 quality gates;
- Acceptance Contract;
- Human-in-the-loop;
- запрет повторного discovery после approval.

## Компромисс

Параллелить можно только сбор фактов и независимые проверки.

Нельзя параллелить действия, которые меняют состояние системы.

```text
Discovery — параллельно.
Execution — последовательно через single-writer.
Verification — параллельно.
Decision / approval — только через Supervisor.
```

## Почему это не конфликтует с текущей логикой

Текущий Token Governor уже разрешает агентов для L3 и L4 задач:

- L3: agents = true, max_agents = 3;
- L4: agents = true, max_agents = 5.

Новая логика не отменяет это. Она уточняет, где именно эти агенты могут работать параллельно.

Task State Machine требует, чтобы после approval задача переходила в execution. Поэтому parallel agents не имеют права возвращать задачу в discovery после approval.

## Архитектура

```text
Telegram / User
   ↓
Task Router
   ↓
Supervisor / Orchestrator
   ↓
Task State Machine
   ↓
Parallel Agent Scheduler
   ↓
Redis Streams / Event Bus
   ↓
Agent Pool
   ├── Pricing Agent
   ├── Config Agent
   ├── Cron Agent
   ├── Provider Status Agent
   ├── Document Agent
   ├── Finance Agent
   ├── Tender Agent
   └── Verification Agent
   ↓
Evidence Cache
   ↓
Bot#2 Gate
   ↓
Human Decision Handler
   ↓
Final Report
```

## Supervisor is the only state owner

Только Supervisor / Orchestrator может менять phase:

- intake;
- discovery;
- plan_ready;
- waiting_user_approval;
- execution;
- verification;
- final_report.

Агенты не меняют phase. Они возвращают только результат subtask.

Пример ответа агента:

```json
{
  "task_id": "task_123",
  "subtask_id": "pricing_check",
  "phase": "discovery",
  "agent": "pricing_agent",
  "status": "done",
  "evidence": [],
  "risks": [],
  "next_action_request": null
}
```

## Discovery fan-out

В discovery Supervisor может запускать несколько независимых агентов:

- Pricing Agent — тарифы, лимиты, стоимость;
- Config Agent — текущие config paths и provider settings;
- Cron Agent — cronjob list и расписания;
- Provider Status Agent — Bothub/Yandex/OpenRouter status;
- Security Agent — проверка, что не раскрываются ключи;
- Context Agent — поиск релевантной памяти и project rules.

Все результаты складываются в Evidence Cache.

Если evidence уже есть, агент не делает повторный tool call, а ссылается на cache.

## Plan assembly

После fan-out Supervisor собирает единый план:

- findings;
- planned changes;
- execution checklist;
- risks;
- rollback notes;
- approval question.

Bot#2 проверяет план по Acceptance Contract.

## Execution is single-writer

Execution нельзя распараллеливать, если действие меняет:

- config;
- env;
- cronjob;
- provider routing;
- token policy;
- memory policy;
- database schema;
- deployment;
- production service.

В execution должен быть lock:

```text
single_writer_lock: task_id + target_resource
```

Пример последовательности:

```text
1. backup config
2. patch config
3. update cronjob
4. reload service
5. run smoke test
```

## Verification fan-out

После execution можно снова параллелить проверки:

- Config Diff Agent;
- Service Health Agent;
- Cron Output Agent;
- Fallback Smoke Test Agent;
- Telegram Report Agent;
- Budget DB Agent.

Но итоговый verdict собирает только Supervisor.

## Bot#2 placement

Bot#2 не должен проверять каждое сообщение каждого агента.

Bot#2 проверяет gates:

- plan_ready;
- before_execution if risk is high;
- verification_result;
- pre_merge_decision;
- human_escalation_request.

## Parallelism limits

Исполняемый контракт хранится в `configs/process_workers.yaml` и
дублируется в `scripts/process_orchestrator.py` как
`parallel_orchestration_policy` process event.

Лимиты:

- L1: no agents;
- L2: no parallel agents by default, only light helper if needed;
- L3: up to 3 agents;
- L4: up to 5 agents;
- verification: up to 3 agents by default;
- per-agent timeout: L2 60s, L3 120s, L4 150s, never above process timeout;
- per-agent token budget: L2 700, L3 900, L4 1200, also capped by token policy;
- BotHub: max 2 parallel calls by default, 12 requests/minute, 250ms cooldown;
- external browser/ssh calls: rate-limited through Tool Gateway;
- write actions: max 1 active writer per resource.

Workspace rule:

```text
/opt/data/agent_workspaces/{process_id}/{agent_id}
```

Agents work in isolated copy-on-write workspaces. Only Supervisor can merge
results into the shared workspace.

SQLite/state rule:

```text
single_writer: supervisor
sqlite_single_writer: true
agent_state_writes_allowed: false
write_queue: supervisor_tool_gateway
```

## Conflict prevention

Агенты не могут:

- менять task phase;
- менять approval state;
- принимать final decision;
- писать в один и тот же config параллельно;
- запускать rollback без Supervisor;
- повторять tool call, если evidence cache уже содержит результат;
- использовать cronjob context для manual task.

## Когда параллельность запрещена

Параллельность запрещена для:

- config write;
- `.env` write;
- cronjob update;
- database migration;
- service restart;
- deployment;
- rollback;
- merge;
- user approval decision;
- memory write of new permanent rule.

## Loop Guard integration

Parallel Agent Scheduler обязан перед каждым tool call спрашивать Tool Gateway:

```text
is_tool_allowed(task_id, phase, tool_name, normalized_args)?
```

Tool Gateway проверяет:

- фазу;
- duplicate fingerprint;
- evidence cache;
- approval state;
- resource lock;
- cron/manual isolation.

Если повтор обнаружен, Scheduler не запускает агента, а возвращает:

```text
LOOP_DETECTED or EVIDENCE_ALREADY_EXISTS
```

## Итоговое правило

Параллельность нужна для скорости, но не для принятия решений.

Агенты собирают факты параллельно.

Supervisor принимает state decisions.

Bot#2 проверяет gates.

Execution пишет изменения строго последовательно.
