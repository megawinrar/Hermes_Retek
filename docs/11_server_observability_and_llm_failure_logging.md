# Server Observability and LLM Failure Logging

## Проблема

Hermes может обновлять данные, выполнять tool calls и потерять связь с LLM-провайдером, например DeepSeek через Bothub.

Если на сервере нет нормального логирования, Bot не понимает:

- на каком task_id упал;
- в какой phase упал;
- какой provider/model использовался;
- был ли timeout, 429, 5xx, network error или stream disconnect;
- был ли retry;
- сработал ли fallback;
- что уже было сделано до падения;
- можно ли продолжить задачу с checkpoint.

## Цель

Сделать server-side observability обязательной частью Hermes runtime.

Bot должен видеть причину падения, а не просто терять связь с моделью.

## Главный принцип

Каждая LLM-сессия, tool call и state transition должны иметь общий correlation id:

```text
trace_id + task_id + phase + provider + model + request_id
```

Без этих полей runtime-событие считается неполным.

## Что логировать обязательно

### 1. Task lifecycle

Логировать:

- task_id;
- task_type;
- user_id или безопасный псевдоним;
- phase;
- previous_phase;
- next_phase;
- trigger;
- timestamp;
- supervisor decision.

### 2. LLM request

Логировать перед запросом:

- provider;
- model;
- base_url host без API key;
- request_id;
- trace_id;
- task_id;
- phase;
- prompt token estimate;
- max_tokens;
- timeout;
- stream true/false;
- fallback_chain.

### 3. LLM response

Логировать после ответа:

- status: success/error/timeout/cancelled;
- latency_ms;
- input_tokens;
- output_tokens;
- cost if available;
- finish_reason;
- provider_request_id if available;
- truncated: true/false.

### 4. LLM failure

Логировать при ошибке:

- error_type;
- error_message_sanitized;
- http_status;
- provider_error_code;
- retry_count;
- will_retry;
- fallback_selected;
- checkpoint_saved;
- last_successful_phase;
- resume_possible.

Типы ошибок:

- `LLM_TIMEOUT`;
- `LLM_STREAM_DISCONNECT`;
- `LLM_RATE_LIMIT`;
- `LLM_AUTH_ERROR`;
- `LLM_PROVIDER_5XX`;
- `LLM_PROVIDER_4XX`;
- `LLM_CONTEXT_LENGTH`;
- `LLM_EMPTY_RESPONSE`;
- `LLM_BAD_JSON`;
- `LLM_NETWORK_ERROR`;
- `LLM_UNKNOWN_ERROR`.

### 5. Tool call

Логировать:

- tool_name;
- normalized_args_hash;
- task_id;
- phase;
- allowed_by_tool_gateway;
- duplicate_fingerprint;
- started_at;
- finished_at;
- status;
- stderr_tail if terminal;
- stdout_tail if safe;
- exit_code;
- affected_resource.

### 6. Fallback decision

Если DeepSeek/Bothub упал, логировать:

- primary_provider;
- primary_model;
- failure_type;
- fallback_provider;
- fallback_model;
- fallback_reason;
- user_notified;
- task_resumed_from_checkpoint.

## Где хранить логи

Минимальная схема:

```text
/opt/data/hermes/logs/hermes-runtime.jsonl
/opt/data/hermes/logs/llm-requests.jsonl
/opt/data/hermes/logs/tool-calls.jsonl
/opt/data/hermes/logs/failures.jsonl
/opt/data/hermes/logs/audit.jsonl
```

Рекомендуемая схема:

- JSONL на диске для быстрого debug;
- SQLite/Postgres для поиска по task_id/trace_id;
- Telegram alert только для важных событий;
- log rotation, чтобы сервер не забился.

## Что нельзя логировать

Запрещено писать в логи:

- API keys;
- full Authorization headers;
- SSH private keys;
- raw `.env`;
- полные персональные данные;
- полные коммерческие документы без необходимости;
- полный prompt, если там есть секреты или клиентские данные.

Нужно логировать sanitized preview и hash.

## Telegram alert

При падении LLM Hermes должен отправить короткое сообщение:

```text
Сообщение от Hermes Runtime

LLM_FAILURE
Task: ...
Phase: ...
Provider: DeepSeek/Bothub
Model: ...
Error: LLM_STREAM_DISCONNECT / LLM_TIMEOUT / ...
Retry: 1/2
Fallback: YandexGPT Lite / disabled
Checkpoint: saved / failed
Resume possible: yes/no

Действие: retry / fallback / stop_and_ask
```

## Checkpoint before risky calls

Перед длинным LLM-запросом или внешним обновлением Hermes обязан сохранить checkpoint:

- task_id;
- phase;
- current plan;
- execution checklist;
- completed steps;
- evidence cache keys;
- last tool result;
- next intended step.

Если LLM падает, задача должна возобновиться с checkpoint, а не начинать discovery заново.

## Retry policy

Рекомендуемая политика:

- timeout/network/stream disconnect: retry до 2 раз;
- 429: exponential backoff и fallback при превышении;
- 401/403: не retry, сразу alert;
- context length: split task or summarize checkpoint;
- bad json: one repair attempt;
- provider 5xx: retry/fallback;
- provider 4xx кроме rate limit/auth: stop and alert.

## Связь с Loop Guard

Если LLM упал во время execution, нельзя автоматически возвращаться в discovery.

Правильный порядок:

1. сохранить failure log;
2. сохранить checkpoint;
3. retry или fallback;
4. продолжить с последнего safe step;
5. если нельзя — остановиться и спросить пользователя.

Возврат в discovery разрешён только если:

- evidence устарел;
- входные данные изменились;
- verification показала противоречие;
- пользователь явно попросил перепроверить.

## Минимальный JSON события ошибки

```json
{
  "event": "llm_failure",
  "trace_id": "trace_...",
  "task_id": "task_...",
  "phase": "execution",
  "provider": "bothub",
  "model": "deepseek-v4-flash",
  "request_id": "req_...",
  "error_type": "LLM_STREAM_DISCONNECT",
  "http_status": null,
  "retry_count": 1,
  "will_retry": true,
  "fallback_selected": "yandexgpt-lite",
  "checkpoint_saved": true,
  "resume_possible": true,
  "timestamp": "2026-06-12T19:30:00+03:00"
}
```

## Итоговое правило

Если Hermes упал из-за LLM, это должно быть видно по task_id, phase, provider, model, request_id, error_type, retry/fallback и checkpoint.

Падение LLM не должно приводить к потере состояния задачи и повторному discovery.
