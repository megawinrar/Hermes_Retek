#!/usr/bin/env python3
"""Shared secret detection and redaction primitives."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SecretPattern:
    name: str
    pattern: re.Pattern[str]


SECRET_PATTERNS: tuple[SecretPattern, ...] = (
    SecretPattern("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}")),
    SecretPattern("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    SecretPattern("github_ghp", re.compile(r"ghp_[A-Za-z0-9_]{20,}")),
    SecretPattern("gitlab_pat", re.compile(r"glpat-[A-Za-z0-9_-]{20,}")),
    SecretPattern("telegram_bot_token", re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b")),
    SecretPattern("bearer_header", re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9_.-]{20,}", re.I)),
    SecretPattern(
        "cookie_assignment",
        re.compile(
            r"(?:(?:\b(?:auth\.sid|auth\.check)\b|\.AspNetCore\.Cookies)\s*(?:=|:)\s*['\"]?[A-Za-z0-9_.:%-]{16,}['\"]?"
            r"|\b(?:sessionid|session_id|cookie)\s*(?:=|:)\s*['\"][^'\"\s]{16,}['\"])",
            re.I,
        ),
    ),
    SecretPattern(
        "secret_assignment",
        re.compile(
            r"\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD)\s*(?:=|:)\s*['\"]?(?!\$\{?)[A-Za-z0-9_.:-]{20,}['\"]?",
            re.I,
        ),
    ),
    SecretPattern("generic_prefixed_token", re.compile(r"\b(?:tok|token|key|sk)_[A-Za-z0-9_-]{20,}\b", re.I)),
    SecretPattern(
        "private_key",
        re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----.*?-----END (?:RSA |OPENSSH |EC )?PRIVATE KEY-----", re.S),
    ),
)


def redact_text(text: str) -> str:
    redacted = text
    for secret_pattern in SECRET_PATTERNS:
        redacted = secret_pattern.pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_payload(item) for key, item in value.items()}
    return value
