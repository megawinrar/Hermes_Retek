#!/usr/bin/env python3
"""User-facing provider error hints for Hermes gateway failures."""

from __future__ import annotations

import argparse
import json
import re
from typing import Any


BOTHUB_HOST_RE = re.compile(r"bothub", re.I)
NOT_ENOUGH_TOKENS_RE = re.compile(
    r"balance is below zero|not_enough_tokens|not enough tokens|insufficient balance|недостаточно",
    re.I,
)


def provider_error_hint(
    *,
    status_code: int | None,
    summary: str = "",
    body: Any = None,
    provider: str = "",
    base_url: str = "",
    model: str = "",
    context_tokens: int | None = None,
    message_count: int | None = None,
) -> str:
    text = " ".join(
        part
        for part in [
            str(summary or ""),
            json.dumps(body, ensure_ascii=False, default=str) if body is not None else "",
        ]
        if part
    )
    context_line = ""
    if context_tokens:
        context_line = f"\n\nКонтекст этого запроса: примерно {context_tokens:,} tokens"
        if message_count:
            context_line += f" / {message_count} messages"
        context_line += "."

    if status_code == 403 and BOTHUB_HOST_RE.search(base_url) and NOT_ENOUGH_TOKENS_RE.search(text):
        return (
            "BotHub отклонил запрос: баланс ниже нуля / NOT_ENOUGH_TOKENS.\n\n"
            f"Провайдер: {provider or 'custom'}\n"
            f"Модель: {model or 'unknown'}\n"
            f"Endpoint: {base_url or 'unknown'}"
            f"{context_line}\n\n"
            "Что сделать: пополнить BotHub/API balance или переключить Hermes на запасного провайдера. "
            "Для длинных задач лучше начать свежую сессию, чтобы не отправлять огромный контекст повторно."
        )

    if status_code in {401, 403}:
        return (
            f"Провайдер отклонил запрос (HTTP {status_code}).\n\n"
            f"Провайдер: {provider or 'unknown'}\n"
            f"Модель: {model or 'unknown'}\n"
            f"Endpoint: {base_url or 'unknown'}\n"
            f"Ошибка: {summary or text or 'unknown'}"
            f"{context_line}"
        )

    if status_code == 429:
        return (
            "Провайдер ограничил запросы или квоту (HTTP 429).\n\n"
            f"Провайдер: {provider or 'unknown'}\n"
            f"Модель: {model or 'unknown'}\n"
            f"Ошибка: {summary or text or 'rate limit'}"
            f"{context_line}"
        )

    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status-code", type=int, default=0)
    parser.add_argument("--summary", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--provider", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--context-tokens", type=int, default=0)
    parser.add_argument("--message-count", type=int, default=0)
    args = parser.parse_args(argv)
    print(
        provider_error_hint(
            status_code=args.status_code or None,
            summary=args.summary,
            body=args.body,
            provider=args.provider,
            base_url=args.base_url,
            model=args.model,
            context_tokens=args.context_tokens or None,
            message_count=args.message_count or None,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
