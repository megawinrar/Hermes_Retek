#!/usr/bin/env python3
"""Site-agnostic parsing policy selection for Hermes browser work."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from typing import Iterable
from urllib.parse import urlparse


EXPORT_RE = re.compile(r"\b(export|excel|xlsx|download|выгруз|скача|эксел)\b", re.I)
AUTH_RE = re.compile(r"\b(login|auth|account|cookie|cookies|кабинет|логин|парол|аккаунт)\b", re.I)


@dataclass(frozen=True)
class ParsingPolicy:
    name: str
    mode: str
    pace_profile: str
    min_delay_seconds: float
    max_delay_seconds: float
    max_parallel_requests: int
    checkpoint_every_actions: int
    requires_ui_seed: bool
    chunk_years: int | None
    fallback_chunk_years: int | None
    evidence_level: str


SITE_POLICIES = {
    "kontur": ParsingPolicy(
        name="kontur",
        mode="ui_seed_then_api_pagination",
        pace_profile="kontur",
        min_delay_seconds=1.25,
        max_delay_seconds=2.5,
        max_parallel_requests=1,
        checkpoint_every_actions=1,
        requires_ui_seed=True,
        chunk_years=2,
        fallback_chunk_years=1,
        evidence_level="high",
    ),
}

DOMAIN_POLICY_HINTS = {
    "zakupki.kontur.ru": "kontur",
    "kontur.ru": "kontur",
}


def normalize_host(url: str) -> str:
    text = (url or "").strip()
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    return (parsed.hostname or "").lower()


def task_requires_auth(task: str) -> bool:
    return bool(AUTH_RE.search(task or ""))


def task_is_export(task: str) -> bool:
    return bool(EXPORT_RE.search(task or ""))


def policy_name_for_host(host: str) -> str:
    for suffix, name in DOMAIN_POLICY_HINTS.items():
        if host == suffix or host.endswith(f".{suffix}"):
            return name
    return ""


def default_policy(*, requires_auth: bool, export_task: bool) -> ParsingPolicy:
    if requires_auth:
        return ParsingPolicy(
            name="default_authenticated",
            mode="browser_first_then_structured_extract",
            pace_profile="cautious",
            min_delay_seconds=2.0,
            max_delay_seconds=5.0,
            max_parallel_requests=1,
            checkpoint_every_actions=1,
            requires_ui_seed=True,
            chunk_years=1 if export_task else None,
            fallback_chunk_years=None,
            evidence_level="high",
        )
    return ParsingPolicy(
        name="default_public",
        mode="structured_fetch_with_browser_fallback",
        pace_profile="human",
        min_delay_seconds=1.0,
        max_delay_seconds=2.0,
        max_parallel_requests=2,
        checkpoint_every_actions=10,
        requires_ui_seed=False,
        chunk_years=1 if export_task else None,
        fallback_chunk_years=None,
        evidence_level="medium",
    )


def select_policy(*, url: str = "", task: str = "", requires_auth: bool | None = None) -> dict[str, object]:
    host = normalize_host(url)
    policy_name = policy_name_for_host(host)
    if policy_name:
        policy = SITE_POLICIES[policy_name]
    else:
        needs_auth = task_requires_auth(task) if requires_auth is None else requires_auth
        policy = default_policy(requires_auth=needs_auth, export_task=task_is_export(task))

    payload = asdict(policy)
    payload["host"] = host
    payload["rules"] = [
        "use one bounded parser worker per site profile unless policy allows more",
        "pace browser and API actions through the selected delay profile",
        "checkpoint URL, query, selector notes, errors, and artifact paths",
        "capture screenshot/source when selectors, exports, or auth state fail",
        "write compact lessons to RLM without raw secrets",
    ]
    return payload


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="")
    parser.add_argument("--task", default="")
    parser.add_argument("--requires-auth", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    print(
        json.dumps(
            select_policy(url=args.url, task=args.task, requires_auth=args.requires_auth or None),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
