#!/usr/bin/env python3
"""Build and optionally send a Hermes timing report from runtime logs."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from secret_patterns import redact_text
except ModuleNotFoundError:  # pragma: no cover - package import path in tests
    from scripts.secret_patterns import redact_text


DEFAULT_GATEWAY_LOG = Path("/opt/data/logs/gateway.log")
DEFAULT_AGENT_LOG = Path("/opt/data/logs/agent.log")
DEFAULT_PROCESS_STORE = Path("/opt/data/process_orchestrator_store.db")
DEFAULT_SUPERVISOR_STORE = Path("/opt/data/supervisor_store.db")

TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(?P<ms>\d{3})")
INBOUND_RE = re.compile(r"inbound message: platform=(?P<platform>\S+) user=(?P<user>.*?) chat=(?P<chat>\S+) msg='(?P<msg>.*)'")
READY_RE = re.compile(
    r"response ready: platform=(?P<platform>\S+) chat=(?P<chat>\S+) "
    r"time=(?P<seconds>[0-9.]+)s api_calls=(?P<api_calls>\d+) response=(?P<chars>\d+) chars"
)
FLUSH_RE = re.compile(r"\[Telegram\] Flushing text batch .*?\((?P<chars>\d+) chars\)")
COMPRESS_START_RE = re.compile(r"Session hygiene: .*auto-compressing")
COMPRESS_DONE_RE = re.compile(r"Session hygiene: compressed .*")
SIGTERM_RE = re.compile(r"Received SIGTERM")
START_RE = re.compile(r"Starting Hermes Gateway")
CONNECTED_RE = re.compile(r"Connected to Telegram")
NETWORK_ERR_RE = re.compile(r"Telegram network error")
NETWORK_RESUME_RE = re.compile(r"Telegram polling resumed after network error")
LLM_START_RE = re.compile(
    r"OpenAI client created \(chat_completion_stream_request.*?thread=(?P<thread>.*?) "
    r"provider=(?P<provider>\S+) base_url=(?P<base_url>\S+) model=(?P<model>\S+)"
)
LLM_DONE_RE = re.compile(
    r"OpenAI client closed \(stream_request_complete.*?thread=(?P<thread>.*?) "
    r"provider=(?P<provider>\S+) base_url=(?P<base_url>\S+) model=(?P<model>\S+)"
)
TOOL_DONE_RE = re.compile(r"agent\.tool_executor: tool (?P<tool>\S+) completed \((?P<seconds>[0-9.]+)s, (?P<chars>\d+) chars\)")
TOOL_ERROR_RE = re.compile(r"agent\.tool_executor: Tool (?P<tool>\S+) returned error \((?P<seconds>[0-9.]+)s\):")
SESSION_PREFIX_RE = re.compile(r"\[(?P<session>[A-Za-z0-9_:-]+)\]")
TURN_CONTEXT_RE = re.compile(
    r"agent\.turn_context: conversation turn: session=(?P<session>\S+) model=(?P<model>\S+) "
    r"provider=(?P<provider>\S+) platform=(?P<platform>\S+) history=(?P<history>\d+) msg='(?P<msg>.*)'"
)
TURN_ENDED_RE = re.compile(
    r"Turn ended: .*?model=(?P<model>\S+) api_calls=(?P<api_used>\d+)/(?P<api_budget>\d+) "
    r"budget=(?P<budget_used>\d+)/(?P<budget_total>\d+) tool_turns=(?P<tool_turns>\d+).*?session=(?P<session>\S+)"
)


@dataclass
class RuntimeTurn:
    inbound_at: datetime
    chat: str
    msg: str
    platform: str = ""
    user: str = ""
    first_flush_at: datetime | None = None
    first_flush_chars: int = 0
    ready_at: datetime | None = None
    response_seconds: float = 0.0
    api_calls: int = 0
    response_chars: int = 0

    @property
    def pending(self) -> bool:
        return self.ready_at is None

    @property
    def first_flush_seconds(self) -> float | None:
        if self.first_flush_at is None:
            return None
        return max(0.0, (self.first_flush_at - self.inbound_at).total_seconds())


@dataclass
class GatewayStats:
    turns: list[RuntimeTurn] = field(default_factory=list)
    network_errors: int = 0
    network_resumes: int = 0
    sigterms: list[datetime] = field(default_factory=list)
    starts: list[datetime] = field(default_factory=list)
    connected: list[datetime] = field(default_factory=list)
    compression_starts: list[datetime] = field(default_factory=list)
    compression_done: list[datetime] = field(default_factory=list)


@dataclass
class LlmCall:
    started_at: datetime
    finished_at: datetime
    seconds: float
    provider: str
    base_url: str
    model: str
    thread: str


@dataclass
class ToolCall:
    at: datetime
    tool: str
    seconds: float
    chars: int = 0
    ok: bool = True
    session: str = ""


@dataclass
class ConversationTurn:
    at: datetime
    session: str
    model: str
    provider: str
    platform: str
    history: int
    msg: str


@dataclass
class TurnEnded:
    at: datetime
    model: str
    api_used: int
    api_budget: int
    tool_turns: int
    session: str


@dataclass
class AgentStats:
    llm_calls: list[LlmCall] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    conversation_turns: list[ConversationTurn] = field(default_factory=list)
    turn_ended: list[TurnEnded] = field(default_factory=list)


def parse_ts(line: str) -> datetime | None:
    match = TS_RE.match(line)
    if not match:
        return None
    raw = f"{match.group('ts')}.{match.group('ms')}"
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)


def read_recent_lines(path: Path, *, since: datetime) -> list[str]:
    if not path.exists():
        return []
    result: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        ts = parse_ts(line)
        if ts is not None and ts >= since:
            result.append(line)
    return result


def parse_gateway(lines: list[str]) -> GatewayStats:
    stats = GatewayStats()
    pending_by_chat: dict[str, RuntimeTurn] = {}
    last_any_pending: RuntimeTurn | None = None

    for line in lines:
        ts = parse_ts(line)
        if ts is None:
            continue

        if NETWORK_ERR_RE.search(line):
            stats.network_errors += 1
        if NETWORK_RESUME_RE.search(line):
            stats.network_resumes += 1
        if SIGTERM_RE.search(line):
            stats.sigterms.append(ts)
        if START_RE.search(line):
            stats.starts.append(ts)
        if CONNECTED_RE.search(line):
            stats.connected.append(ts)
        if COMPRESS_START_RE.search(line):
            stats.compression_starts.append(ts)
        if COMPRESS_DONE_RE.search(line):
            stats.compression_done.append(ts)

        inbound = INBOUND_RE.search(line)
        if inbound:
            turn = RuntimeTurn(
                inbound_at=ts,
                chat=inbound.group("chat"),
                msg=inbound.group("msg"),
                platform=inbound.group("platform"),
                user=inbound.group("user"),
            )
            stats.turns.append(turn)
            pending_by_chat[turn.chat] = turn
            last_any_pending = turn
            continue

        flush = FLUSH_RE.search(line)
        if flush and last_any_pending and last_any_pending.first_flush_at is None:
            last_any_pending.first_flush_at = ts
            last_any_pending.first_flush_chars = int(flush.group("chars"))
            continue

        ready = READY_RE.search(line)
        if ready:
            chat = ready.group("chat")
            turn = pending_by_chat.get(chat) or last_any_pending
            if turn and turn.ready_at is None:
                turn.ready_at = ts
                turn.response_seconds = float(ready.group("seconds"))
                turn.api_calls = int(ready.group("api_calls"))
                turn.response_chars = int(ready.group("chars"))
                pending_by_chat.pop(chat, None)
                if last_any_pending is turn:
                    last_any_pending = None

    return stats


def parse_agent(lines: list[str]) -> AgentStats:
    stats = AgentStats()
    pending_llm: dict[str, tuple[datetime, str, str, str]] = {}

    for line in lines:
        ts = parse_ts(line)
        if ts is None:
            continue
        session_prefix = SESSION_PREFIX_RE.search(line)
        session = session_prefix.group("session") if session_prefix else ""

        llm_start = LLM_START_RE.search(line)
        if llm_start:
            pending_llm[llm_start.group("thread")] = (
                ts,
                llm_start.group("provider"),
                llm_start.group("base_url"),
                llm_start.group("model"),
            )
            continue

        llm_done = LLM_DONE_RE.search(line)
        if llm_done:
            thread = llm_done.group("thread")
            started = pending_llm.pop(thread, None)
            if started:
                started_at, provider, base_url, model = started
                stats.llm_calls.append(
                    LlmCall(
                        started_at=started_at,
                        finished_at=ts,
                        seconds=max(0.0, (ts - started_at).total_seconds()),
                        provider=provider,
                        base_url=base_url,
                        model=model,
                        thread=thread,
                    )
                )
            continue

        tool_done = TOOL_DONE_RE.search(line)
        if tool_done:
            stats.tool_calls.append(
                ToolCall(
                    at=ts,
                    tool=tool_done.group("tool"),
                    seconds=float(tool_done.group("seconds")),
                    chars=int(tool_done.group("chars")),
                    ok=True,
                    session=session,
                )
            )
            continue

        tool_error = TOOL_ERROR_RE.search(line)
        if tool_error:
            stats.tool_calls.append(
                ToolCall(
                    at=ts,
                    tool=tool_error.group("tool"),
                    seconds=float(tool_error.group("seconds")),
                    ok=False,
                    session=session,
                )
            )
            continue

        turn_context = TURN_CONTEXT_RE.search(line)
        if turn_context:
            stats.conversation_turns.append(
                ConversationTurn(
                    at=ts,
                    session=turn_context.group("session"),
                    model=turn_context.group("model"),
                    provider=turn_context.group("provider"),
                    platform=turn_context.group("platform"),
                    history=int(turn_context.group("history")),
                    msg=turn_context.group("msg"),
                )
            )
            continue

        turn_ended = TURN_ENDED_RE.search(line)
        if turn_ended:
            stats.turn_ended.append(
                TurnEnded(
                    at=ts,
                    model=turn_ended.group("model"),
                    api_used=int(turn_ended.group("api_used")),
                    api_budget=int(turn_ended.group("api_budget")),
                    tool_turns=int(turn_ended.group("tool_turns")),
                    session=turn_ended.group("session"),
                )
            )

    return stats


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return ordered[index]


def timing_summary(values: list[float]) -> str:
    if not values:
        return "нет данных"
    return (
        f"avg {statistics.mean(values):.1f}s / "
        f"median {statistics.median(values):.1f}s / "
        f"p95 {percentile(values, 0.95):.1f}s / "
        f"max {max(values):.1f}s"
    )


def numeric_summary(values: list[float]) -> str:
    if not values:
        return "нет данных"
    return (
        f"avg {statistics.mean(values):.1f} / "
        f"median {statistics.median(values):.1f} / "
        f"p95 {percentile(values, 0.95):.1f} / "
        f"max {max(values):.1f}"
    )


def short(text: str, limit: int = 90) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        return "<empty>"
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "..."


def telegram_chunks(text: str, limit: int = 3800) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        extra = len(line) + (1 if current else 0)
        if current and current_len + extra > limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_len = 0
            for start in range(0, len(line), limit):
                chunks.append(line[start : start + limit])
            continue
        current.append(line)
        current_len += extra
    if current:
        chunks.append("\n".join(current))
    return chunks


def table_counts(db_path: Path, table: str, since: datetime) -> dict[str, int]:
    if not db_path.exists():
        return {}
    try:
        con = sqlite3.connect(str(db_path))
        rows = con.execute(
            f"SELECT status, COUNT(*) FROM {table} WHERE created_at >= ? GROUP BY status",
            (since.isoformat(timespec="seconds"),),
        ).fetchall()
    except Exception:
        return {}
    return {str(status): int(count) for status, count in rows}


def active_rows(db_path: Path, table: str, columns: list[str]) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        cols = ", ".join(columns)
        rows = con.execute(
            f"""
            SELECT {cols}
            FROM {table}
            WHERE status IN ('running', 'awaiting_human_decision', 'return_to_bot1')
            ORDER BY updated_at DESC
            LIMIT 5
            """
        ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def restart_downtimes(stats: GatewayStats) -> list[float]:
    starts = sorted(stats.starts)
    result: list[float] = []
    for sigterm in sorted(stats.sigterms):
        next_start = next((item for item in starts if item >= sigterm), None)
        if next_start:
            result.append((next_start - sigterm).total_seconds())
    return result


def top_counts(values: list[str], limit: int = 3) -> str:
    if not values:
        return "нет"
    return ", ".join(f"{name}={count}" for name, count in Counter(values).most_common(limit))


def agent_sessions(agent_stats: AgentStats) -> set[str]:
    sessions = {turn.session for turn in agent_stats.conversation_turns if turn.session}
    sessions.update(turn.session for turn in agent_stats.turn_ended if turn.session)
    sessions.update(call.session for call in agent_stats.tool_calls if call.session)
    return sessions


def busiest_sessions(agent_stats: AgentStats, limit: int = 5) -> list[str]:
    rows: dict[str, dict[str, float]] = {}
    for session in agent_sessions(agent_stats):
        rows[session] = {"turns": 0, "tools": 0, "errors": 0, "delegate_seconds": 0.0}

    for turn in agent_stats.turn_ended:
        if turn.session:
            rows.setdefault(turn.session, {"turns": 0, "tools": 0, "errors": 0, "delegate_seconds": 0.0})["turns"] += 1
    for call in agent_stats.tool_calls:
        if not call.session:
            continue
        row = rows.setdefault(call.session, {"turns": 0, "tools": 0, "errors": 0, "delegate_seconds": 0.0})
        row["tools"] += 1
        if not call.ok:
            row["errors"] += 1
        if call.tool == "delegate_task":
            row["delegate_seconds"] += call.seconds

    def score(item: tuple[str, dict[str, float]]) -> tuple[float, float, float, str]:
        session, values = item
        return (values["delegate_seconds"], values["errors"], values["tools"] + values["turns"], session)

    result: list[str] = []
    for session, values in sorted(rows.items(), key=score, reverse=True)[:limit]:
        delegate = f", delegate={values['delegate_seconds']:.1f}s" if values["delegate_seconds"] else ""
        result.append(f"{short(session, 34)}: turns={int(values['turns'])}, tools={int(values['tools'])}, errors={int(values['errors'])}{delegate}")
    return result


def diagnose(stats: GatewayStats, agent_stats: AgentStats) -> list[str]:
    completed = [turn for turn in stats.turns if not turn.pending]
    slow = [turn for turn in completed if turn.response_seconds >= 120]
    high_api = [turn for turn in completed if turn.api_calls >= 8]
    slow_first = [turn for turn in completed if (turn.first_flush_seconds or 0) >= 45]
    llm_durations = [call.seconds for call in agent_stats.llm_calls]
    slow_llm = [value for value in llm_durations if value >= 30]
    tool_errors = [call for call in agent_stats.tool_calls if not call.ok]
    slow_tools = [call for call in agent_stats.tool_calls if call.seconds >= 20]
    tool_heavy_turns = [turn for turn in agent_stats.turn_ended if turn.tool_turns >= 40]
    delegated = [call for call in agent_stats.tool_calls if call.tool == "delegate_task"]
    result: list[str] = []

    if slow:
        result.append("Долгие ответы совпадают с большим числом agent/api шагов: это чаще LLM/tool-loop, а не Telegram.")
    if high_api:
        result.append("Есть turns с api_calls >= 8: Hermes делает много внутренних итераций перед финалом.")
    if slow_first:
        result.append("Есть задержка до первого streaming flush >= 45s: пользователь долго ждёт первый видимый текст.")
    if slow_llm:
        result.append("Есть LLM stream calls >= 30s: часть задержки реально сидит в ожидании BotHub/модели.")
    if slow_tools:
        result.append("Есть tool calls >= 20s: часть времени уходит не в LLM, а в инструменты/сетевые проверки.")
    if tool_errors:
        result.append("Есть tool errors: повторные неудачные инструменты раздувают loop и число api_calls.")
    if tool_heavy_turns:
        result.append("Есть turns с tool_turns >= 40: агент долго ходит по инструментам до финального ответа.")
    if delegated:
        result.append("Есть delegate_task: Hermes уже поднимает дочерних агентов; их fan-out нужно лимитировать и логировать отдельно.")
    if stats.network_errors:
        result.append("Были Telegram network reconnects; если они попадают внутрь turn, они добавляют видимую задержку.")
    if stats.compression_starts:
        result.append("Была session compression; на больших контекстах она может добавить десятки секунд перед ответом.")
    if stats.sigterms:
        result.append("Были SIGTERM/restart gateway; это даёт короткий простой и прерывает активные задачи.")
    if not result:
        result.append("Критичных лагов по gateway.log не видно; основные задержки надо искать в LLM/tool timings конкретных задач.")
    return result


def build_report(
    *,
    stats: GatewayStats,
    agent_stats: AgentStats,
    process_counts: dict[str, int],
    supervisor_counts: dict[str, int],
    process_active: list[dict[str, Any]],
    supervisor_active: list[dict[str, Any]],
    hours: int,
    now: datetime,
) -> str:
    completed = [turn for turn in stats.turns if not turn.pending]
    pending = [turn for turn in stats.turns if turn.pending]
    durations = [turn.response_seconds for turn in completed]
    api_calls = [turn.api_calls for turn in completed]
    first_flushes = [turn.first_flush_seconds for turn in completed if turn.first_flush_seconds is not None]
    slowest = sorted(completed, key=lambda turn: turn.response_seconds, reverse=True)[:5]
    downtimes = restart_downtimes(stats)
    llm_durations = [call.seconds for call in agent_stats.llm_calls]
    tool_calls = [call for call in agent_stats.tool_calls if call.ok]
    tool_errors = [call for call in agent_stats.tool_calls if not call.ok]
    tool_durations = [call.seconds for call in tool_calls]
    slow_tools = sorted(agent_stats.tool_calls, key=lambda call: call.seconds, reverse=True)[:5]
    turn_tool_counts = [turn.tool_turns for turn in agent_stats.turn_ended]
    sessions = agent_sessions(agent_stats)
    delegated = [call for call in agent_stats.tool_calls if call.tool == "delegate_task"]
    background_reviews = [
        turn
        for turn in agent_stats.conversation_turns
        if turn.msg.startswith("Review the conversation above") or "update the skill library" in turn.msg
    ]
    session_histories = [float(turn.history) for turn in agent_stats.conversation_turns]
    turn_api_used = [float(turn.api_used) for turn in agent_stats.turn_ended]

    lines = [
        "Отчёт Hermes по таймингам",
        f"Период: последние {hours}ч до {now.strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Итог",
        f"- входящих Telegram turns: {len(stats.turns)}",
        f"- завершено: {len(completed)}, ещё без финального response ready: {len(pending)}",
        f"- длительность ответа: {timing_summary(durations)}",
        f"- среднее api_calls: {statistics.mean(api_calls):.1f}" if api_calls else "- среднее api_calls: 0",
        f"- первый streaming flush: median {statistics.median(first_flushes):.1f}s, max {max(first_flushes):.1f}s" if first_flushes else "- первый streaming flush: нет данных",
        "",
        "LLM/BotHub",
        f"- stream calls: {len(agent_stats.llm_calls)}",
        f"- stream latency: {timing_summary(llm_durations)}",
        f"- модели: {top_counts([call.model for call in agent_stats.llm_calls])}",
        f"- base_url: {top_counts([call.base_url for call in agent_stats.llm_calls], limit=2)}",
        "",
        "Agents",
        f"- agent sessions seen: {len(sessions)}",
        f"- conversation turns by platform: {top_counts([turn.platform for turn in agent_stats.conversation_turns])}",
        f"- finished agent turns: {len(agent_stats.turn_ended)}",
        f"- agent api_used per finished turn: {numeric_summary(turn_api_used)}",
        f"- agent history size: {numeric_summary(session_histories)}",
        f"- delegate_task calls: {len(delegated)}, latency: {timing_summary([call.seconds for call in delegated])}",
        f"- background review turns: {len(background_reviews)}",
        "",
        "Tools",
        f"- completed/error calls: {len(tool_calls)}/{len(tool_errors)}",
        f"- tool latency: {timing_summary(tool_durations)}",
        f"- tool errors: {top_counts([call.tool for call in tool_errors])}",
        f"- tool_turns per finished turn: {numeric_summary([float(value) for value in turn_tool_counts])}",
        "",
        "Инфраструктура",
        f"- Telegram network errors/resumes: {stats.network_errors}/{stats.network_resumes}",
        f"- gateway SIGTERM/start events: {len(stats.sigterms)}/{len(stats.starts)}",
        f"- простой после SIGTERM: avg {statistics.mean(downtimes):.1f}s, max {max(downtimes):.1f}s" if downtimes else "- простой после SIGTERM: нет",
        f"- session compression: {len(stats.compression_starts)} start, {len(stats.compression_done)} done",
        "",
        "Process/Supervisor",
        f"- process_runs по статусам: {process_counts or {}}",
        f"- supervisor_tasks по статусам: {supervisor_counts or {}}",
        f"- активные process_runs: {len(process_active)}",
        f"- активные supervisor_tasks: {len(supervisor_active)}",
        "",
        "Самые долгие turns",
    ]
    if slowest:
        for idx, turn in enumerate(slowest, 1):
            flush = turn.first_flush_seconds
            flush_text = f", first_flush={flush:.1f}s" if flush is not None else ""
            lines.append(
                f"{idx}. {turn.response_seconds:.1f}s, api_calls={turn.api_calls}{flush_text}, "
                f"chars={turn.response_chars}: {short(turn.msg)}"
            )
    else:
        lines.append("- нет завершённых turns")

    lines.extend(["", "Самые шумные agent sessions"])
    noisy_sessions = busiest_sessions(agent_stats)
    if noisy_sessions:
        lines.extend(f"{idx}. {item}" for idx, item in enumerate(noisy_sessions, 1))
    else:
        lines.append("- нет agent sessions")

    lines.extend(["", "Самые долгие tools"])
    if slow_tools:
        for idx, call in enumerate(slow_tools, 1):
            status = "ok" if call.ok else "error"
            lines.append(f"{idx}. {call.seconds:.1f}s, {status}, {call.tool}")
    else:
        lines.append("- нет tool calls")

    lines.extend(["", "Почему могло лагать"])
    lines.extend(f"- {item}" for item in diagnose(stats, agent_stats))
    return redact_text("\n".join(lines))


def build_json(stats: GatewayStats, agent_stats: AgentStats, *, hours: int, now: datetime) -> dict[str, Any]:
    completed = [turn for turn in stats.turns if not turn.pending]
    durations = [turn.response_seconds for turn in completed]
    llm_durations = [call.seconds for call in agent_stats.llm_calls]
    tool_errors = [call for call in agent_stats.tool_calls if not call.ok]
    delegated = [call for call in agent_stats.tool_calls if call.tool == "delegate_task"]
    return {
        "hours": hours,
        "generated_at": now.isoformat(timespec="seconds"),
        "turn_count": len(stats.turns),
        "completed_count": len(completed),
        "pending_count": len([turn for turn in stats.turns if turn.pending]),
        "avg_seconds": statistics.mean(durations) if durations else 0,
        "p95_seconds": percentile(durations, 0.95),
        "max_seconds": max(durations, default=0),
        "network_errors": stats.network_errors,
        "sigterms": len(stats.sigterms),
        "compression_count": len(stats.compression_starts),
        "llm_call_count": len(agent_stats.llm_calls),
        "llm_avg_seconds": statistics.mean(llm_durations) if llm_durations else 0,
        "llm_p95_seconds": percentile(llm_durations, 0.95),
        "tool_call_count": len(agent_stats.tool_calls),
        "tool_error_count": len(tool_errors),
        "agent_session_count": len(agent_sessions(agent_stats)),
        "agent_turn_count": len(agent_stats.turn_ended),
        "delegate_task_count": len(delegated),
        "delegate_task_max_seconds": max((call.seconds for call in delegated), default=0),
    }


def send_report_to_telegram(report: str) -> dict[str, Any]:
    from devlog import send_telegram_message

    chunks = telegram_chunks(report)
    deliveries = []
    for index, chunk in enumerate(chunks, 1):
        prefix = f"Часть {index}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
        deliveries.append(send_telegram_message(prefix + chunk))
    return {
        "delivered": all(item.get("delivered") for item in deliveries),
        "chunks": len(chunks),
        "deliveries": deliveries,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-log", type=Path, default=DEFAULT_GATEWAY_LOG)
    parser.add_argument("--agent-log", type=Path, default=DEFAULT_AGENT_LOG)
    parser.add_argument("--process-store", type=Path, default=DEFAULT_PROCESS_STORE)
    parser.add_argument("--supervisor-store", type=Path, default=DEFAULT_SUPERVISOR_STORE)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=max(1, args.hours))
    stats = parse_gateway(read_recent_lines(args.gateway_log, since=since))
    agent_stats = parse_agent(read_recent_lines(args.agent_log, since=since))
    process_counts = table_counts(args.process_store, "process_runs", since)
    supervisor_counts = table_counts(args.supervisor_store, "supervisor_tasks", since)
    process_active = active_rows(args.process_store, "process_runs", ["id", "status", "current_phase", "updated_at", "task"])
    supervisor_active = active_rows(args.supervisor_store, "supervisor_tasks", ["id", "status", "updated_at", "tz"])

    if args.json_output:
        print(json.dumps(build_json(stats, agent_stats, hours=args.hours, now=now), ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    report = build_report(
        stats=stats,
        agent_stats=agent_stats,
        process_counts=process_counts,
        supervisor_counts=supervisor_counts,
        process_active=process_active,
        supervisor_active=supervisor_active,
        hours=args.hours,
        now=now,
    )
    print(report)

    if args.send_telegram:
        delivered = send_report_to_telegram(report)
        print(json.dumps({"telegram_delivery": delivered}, ensure_ascii=False, sort_keys=True))
        return 0 if delivered["delivered"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
