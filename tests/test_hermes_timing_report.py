"""Tests for Hermes runtime timing report."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.hermes_timing_report import AgentStats, agent_sessions, build_json, build_report, operational_actions, parse_agent, parse_gateway, read_recent_lines, telegram_chunks  # noqa: E402


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_parse_gateway_turn_with_flush_and_ready():
    stats = parse_gateway(
        [
            "2026-06-14 03:00:00,000 INFO inbound message: platform=telegram user=Mr. Di chat=123 msg='Проверь скорость'",
            "2026-06-14 03:00:04,000 INFO [Telegram] Flushing text batch for chat=123 (44 chars)",
            "2026-06-14 03:00:23,800 INFO response ready: platform=telegram chat=123 time=23.8s api_calls=3 response=1200 chars",
        ]
    )

    assert len(stats.turns) == 1
    turn = stats.turns[0]
    assert not turn.pending
    assert turn.response_seconds == 23.8
    assert turn.api_calls == 3
    assert turn.first_flush_seconds == 4.0
    assert turn.msg == "Проверь скорость"


def test_parse_gateway_infrastructure_events():
    stats = parse_gateway(
        [
            "2026-06-14 03:42:28,000 WARNING Received SIGTERM",
            "2026-06-14 03:42:38,000 INFO Starting Hermes Gateway",
            "2026-06-14 03:42:40,000 INFO Connected to Telegram",
            "2026-06-14 03:45:00,000 WARNING Telegram network error: timeout",
            "2026-06-14 03:45:09,000 INFO Telegram polling resumed after network error",
            "2026-06-14 03:48:00,000 INFO Session hygiene: auto-compressing session abc",
            "2026-06-14 03:48:12,000 INFO Session hygiene: compressed session abc",
        ]
    )

    assert len(stats.sigterms) == 1
    assert len(stats.starts) == 1
    assert len(stats.connected) == 1
    assert stats.network_errors == 1
    assert stats.network_resumes == 1
    assert len(stats.compression_starts) == 1
    assert len(stats.compression_done) == 1


def test_build_report_handles_empty_window():
    report = build_report(
        stats=parse_gateway([]),
        agent_stats=AgentStats(),
        process_counts={},
        supervisor_counts={},
        process_active=[],
        supervisor_active=[],
        hours=24,
        now=utc("2026-06-14T12:00:00"),
    )

    assert "Отчёт Hermes по таймингам" in report
    assert "длительность ответа: нет данных" in report
    assert "нет завершённых turns" in report


def test_build_report_diagnoses_high_api_and_network():
    stats = parse_gateway(
        [
            "2026-06-14 03:00:00,000 INFO inbound message: platform=telegram user=u chat=1 msg='долгая задача'",
            "2026-06-14 03:01:00,000 INFO [Telegram] Flushing text batch for chat=1 (10 chars)",
            "2026-06-14 03:03:00,000 INFO response ready: platform=telegram chat=1 time=180.0s api_calls=9 response=200 chars",
            "2026-06-14 03:03:05,000 WARNING Telegram network error: timeout",
        ]
    )

    report = build_report(
        stats=stats,
        agent_stats=parse_agent(
            [
                "2026-06-14 03:00:00,000 INFO run_agent: OpenAI client created (chat_completion_stream_request, shared=False) thread=Thread-1 (_call):abc provider=custom base_url=https://openai.bothub.chat/v1 model=deepseek-v4-flash",
                "2026-06-14 03:00:35,000 INFO run_agent: OpenAI client closed (stream_request_complete, shared=False, tcp_force_closed=0) thread=Thread-1 (_call):abc provider=custom base_url=https://openai.bothub.chat/v1 model=deepseek-v4-flash",
                "2026-06-14 03:00:36,000 INFO [s] agent.turn_context: conversation turn: session=s model=deepseek-v4-flash provider=custom platform=telegram history=12 msg='долгая задача'",
                "2026-06-14 03:01:00,000 WARNING [s] agent.tool_executor: Tool execute_code returned error (30.27s): boom",
                "2026-06-14 03:01:10,000 INFO [s] agent.tool_executor: tool delegate_task completed (45.00s, 1000 chars)",
                "2026-06-14 03:01:40,000 INFO [s] agent.conversation_loop: Turn ended: reason=text_response(finish_reason=stop) model=deepseek-v4-flash api_calls=9/16 budget=9/16 tool_turns=44 last_msg_role=assistant response_len=1033 session=s",
            ]
        ),
        process_counts={"done": 1},
        supervisor_counts={"approved": 1},
        process_active=[],
        supervisor_active=[],
        hours=24,
        now=utc("2026-06-14T12:00:00"),
    )

    assert "180.0s, api_calls=9" in report
    assert "api_calls >= 8" in report
    assert "Telegram network reconnects" in report
    assert "LLM stream calls >= 30s" in report
    assert "tool errors" in report
    assert "tool_turns per finished turn: avg 44.0 / median 44.0" in report
    assert "agent sessions seen: 1" in report
    assert "delegate_task calls: 1" in report
    assert "Есть delegate_task" in report
    assert "Что чинить первым" in report
    assert "progress/ack" in report


def test_build_json_handles_no_completed_turns():
    stats = parse_gateway(
        [
            "2026-06-14 03:00:00,000 INFO inbound message: platform=telegram user=u chat=1 msg='ещё выполняется'",
        ]
    )

    payload = build_json(stats, AgentStats(), hours=1, now=utc("2026-06-14T12:00:00"))

    assert payload["turn_count"] == 1
    assert payload["completed_count"] == 0
    assert payload["pending_count"] == 1
    assert payload["avg_seconds"] == 0
    assert payload["agent_session_count"] == 0
    assert payload["delegate_task_count"] == 0
    assert payload["first_flush_max_seconds"] == 0
    assert payload["api_calls_max"] == 0
    assert payload["tool_turns_max"] == 0


def test_operational_actions_cover_current_runtime_bottlenecks():
    gateway = parse_gateway(
        [
            "2026-06-15 06:00:00,000 INFO inbound message: platform=telegram user=u chat=1 msg='залей веткой'",
            "2026-06-15 06:16:35,700 INFO [Telegram] Flushing text batch for chat=1 (44 chars)",
            "2026-06-15 06:37:49,300 INFO response ready: platform=telegram chat=1 time=2269.3s api_calls=49 response=1561 chars",
            "2026-06-15 06:38:00,000 WARNING Received SIGTERM",
            "2026-06-15 06:38:09,000 INFO Starting Hermes Gateway",
            "2026-06-15 06:39:00,000 INFO Session hygiene: auto-compressing session s",
        ]
    )
    agent = parse_agent(
        [
            "2026-06-15 06:00:00,000 INFO run_agent: OpenAI client created (chat_completion_stream_request, shared=False) thread=t1 provider=custom base_url=https://openai.bothub.chat/v1 model=deepseek-v4-flash",
            "2026-06-15 06:01:13,000 INFO run_agent: OpenAI client closed (stream_request_complete, shared=False, tcp_force_closed=0) thread=t1 provider=custom base_url=https://openai.bothub.chat/v1 model=deepseek-v4-flash",
            "2026-06-15 06:01:14,000 INFO [s] agent.tool_executor: tool delegate_task completed (393.60s, 1000 chars)",
            "2026-06-15 06:01:15,000 WARNING [s] agent.tool_executor: Tool execute_code returned error (3.21s): bad",
            "2026-06-15 06:01:16,000 WARNING [s] agent.tool_executor: Tool memory returned error (0.01s): bad",
            "2026-06-15 06:01:17,000 WARNING [s] agent.tool_executor: Tool browser_navigate returned error (8.00s): bad",
            "2026-06-15 06:01:18,000 WARNING [s] agent.tool_executor: Tool execute_code returned error (3.21s): bad",
            "2026-06-15 06:01:19,000 WARNING [s] agent.tool_executor: Tool execute_code returned error (3.21s): bad",
            "2026-06-15 06:01:20,000 INFO [s] agent.conversation_loop: Turn ended: reason=text_response(finish_reason=stop) model=deepseek-v4-flash api_calls=49/64 budget=49/64 tool_turns=221 last_msg_role=assistant response_len=1033 session=s",
        ]
    )

    actions = "\n".join(operational_actions(gateway, agent))

    assert "first flush <= 10s" in actions
    assert "hard cap на внутренние LLM-итерации" in actions
    assert "hard cap на tool_turns" in actions
    assert "delegate_task: timeout 120s" in actions
    assert "BotHub p95>=60s" in actions
    assert "рестарты во время активных turns" in actions
    assert "session compression" in actions


def test_build_json_exposes_guardrail_metrics():
    gateway = parse_gateway(
        [
            "2026-06-15 06:00:00,000 INFO inbound message: platform=telegram user=u chat=1 msg='slow'",
            "2026-06-15 06:02:00,000 INFO [Telegram] Flushing text batch for chat=1 (44 chars)",
            "2026-06-15 06:04:00,000 INFO response ready: platform=telegram chat=1 time=240.0s api_calls=16 response=500 chars",
        ]
    )
    agent = parse_agent(
        [
            "2026-06-15 06:00:00,000 INFO [s] agent.tool_executor: tool delegate_task completed (121.00s, 1000 chars)",
            "2026-06-15 06:03:00,000 INFO [s] agent.conversation_loop: Turn ended: reason=text_response(finish_reason=stop) model=deepseek-v4-flash api_calls=16/64 budget=16/64 tool_turns=88 last_msg_role=assistant response_len=500 session=s",
        ]
    )

    payload = build_json(gateway, agent, hours=24, now=utc("2026-06-15T07:00:00"))

    assert payload["first_flush_max_seconds"] == 120.0
    assert payload["api_calls_max"] == 16
    assert payload["tool_turns_max"] == 88
    assert payload["delegate_task_max_seconds"] == 121.0
    assert payload["operational_action_count"] >= 4


def test_parse_agent_llm_tool_and_turn_timing():
    stats = parse_agent(
        [
            "2026-06-14 18:14:12,799 INFO run_agent: OpenAI client created (chat_completion_stream_request, shared=False) thread=Thread-287 (_call):139 provider=custom base_url=https://openai.bothub.chat/v1 model=deepseek-v4-flash",
            "2026-06-14 18:14:50,073 INFO run_agent: OpenAI client closed (stream_request_complete, shared=False, tcp_force_closed=0) thread=Thread-287 (_call):139 provider=custom base_url=https://openai.bothub.chat/v1 model=deepseek-v4-flash",
            "2026-06-14 18:14:50,100 INFO [s] agent.turn_context: conversation turn: session=s model=deepseek-v4-flash provider=custom platform=telegram history=202 msg='Review the conversation above and update the skill library.'",
            "2026-06-14 18:14:50,105 INFO [s] agent.tool_executor: tool skill_view completed (0.03s, 311 chars)",
            "2026-06-14 18:15:14,414 INFO [s] agent.tool_executor: tool skill_manage completed (0.00s, 265 chars)",
            "2026-06-14 18:15:14,450 INFO [s] agent.tool_executor: tool delegate_task completed (393.61s, 5976 chars)",
            "2026-06-14 18:15:14,500 WARNING [s] agent.tool_executor: Tool execute_code returned error (3.21s): bad",
            "2026-06-14 18:15:53,575 INFO [s] agent.conversation_loop: Turn ended: reason=text_response(finish_reason=stop) model=deepseek-v4-flash api_calls=5/16 budget=5/16 tool_turns=93 last_msg_role=assistant response_len=1033 session=s",
        ]
    )

    assert len(stats.llm_calls) == 1
    assert stats.llm_calls[0].seconds == 37.274
    assert stats.llm_calls[0].base_url == "https://openai.bothub.chat/v1"
    assert len(stats.conversation_turns) == 1
    assert stats.conversation_turns[0].platform == "telegram"
    assert len(stats.tool_calls) == 4
    assert [call.ok for call in stats.tool_calls] == [True, True, True, False]
    assert [call.session for call in stats.tool_calls] == ["s", "s", "s", "s"]
    assert stats.turn_ended[0].api_used == 5
    assert stats.turn_ended[0].tool_turns == 93
    assert agent_sessions(stats) == {"s"}


def test_read_recent_lines_filters_by_timestamp(tmp_path):
    log_path = tmp_path / "gateway.log"
    log_path.write_text(
        "\n".join(
            [
                "2026-06-14 02:59:59,999 INFO old",
                "2026-06-14 03:00:00,000 INFO fresh",
                "line without timestamp",
            ]
        ),
        encoding="utf-8",
    )

    lines = read_recent_lines(log_path, since=utc("2026-06-14T03:00:00"))

    assert lines == ["2026-06-14 03:00:00,000 INFO fresh"]


def test_read_recent_lines_accepts_docker_timestamp_prefix(tmp_path):
    log_path = tmp_path / "docker.log"
    log_path.write_text(
        "\n".join(
            [
                "2026-06-14T02:59:59.999999999Z INFO old",
                "2026-06-14T03:00:00.123456789Z INFO inbound message: platform=telegram user=u chat=1 msg='fresh'",
            ]
        ),
        encoding="utf-8",
    )

    lines = read_recent_lines(log_path, since=utc("2026-06-14T03:00:00"))
    stats = parse_gateway(lines)

    assert lines == ["2026-06-14T03:00:00.123456789Z INFO inbound message: platform=telegram user=u chat=1 msg='fresh'"]
    assert len(stats.turns) == 1
    assert stats.turns[0].msg == "fresh"


def test_telegram_chunks_keep_messages_under_limit():
    text = "\n".join([f"line {idx} " + ("x" * 40) for idx in range(20)])

    chunks = telegram_chunks(text, limit=120)

    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert "line 0" in chunks[0]
    assert "line 19" in chunks[-1]
