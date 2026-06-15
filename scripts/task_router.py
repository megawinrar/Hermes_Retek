#!/usr/bin/env python3
"""Deterministic Hermes task router for process orchestration."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from typing import Any


LEVEL_DEFAULTS: dict[str, dict[str, Any]] = {
    "L0": {
        "model_class": "none",
        "needs_memory": False,
        "memory_top_k": 0,
        "needs_tools": True,
        "needs_agents": False,
        "max_agents": 0,
        "max_rounds": 0,
        "max_input_tokens": 0,
        "max_output_tokens": 0,
    },
    "L1": {
        "model_class": "cheap_fast",
        "needs_memory": False,
        "memory_top_k": 0,
        "needs_tools": False,
        "needs_agents": False,
        "max_agents": 0,
        "max_rounds": 1,
        "max_input_tokens": 3000,
        "max_output_tokens": 800,
    },
    "L2": {
        "model_class": "medium",
        "needs_memory": True,
        "memory_top_k": 5,
        "needs_tools": True,
        "needs_agents": False,
        "max_agents": 0,
        "max_rounds": 1,
        "max_input_tokens": 8000,
        "max_output_tokens": 2000,
    },
    "L3": {
        "model_class": "strong_reasoning",
        "needs_memory": True,
        "memory_top_k": 8,
        "needs_tools": True,
        "needs_agents": True,
        "max_agents": 3,
        "max_rounds": 2,
        "max_input_tokens": 16000,
        "max_output_tokens": 4000,
    },
    "L4": {
        "model_class": "strong_reasoning",
        "needs_memory": True,
        "memory_top_k": 12,
        "needs_tools": True,
        "needs_agents": True,
        "max_agents": 5,
        "max_rounds": 3,
        "max_input_tokens": 24000,
        "max_output_tokens": 6000,
    },
}
LEVEL_RANK = {level: index for index, level in enumerate(["L0", "L1", "L2", "L3", "L4"])}
RISK_RANK = {"low": 0, "medium": 1, "high": 2}

PROCESS_PLAN: dict[str, list[str]] = {
    "L0": ["router", "supervisor"],
    "L1": ["router", "supervisor", "bot1"],
    "L2": ["router", "supervisor", "bot1", "tester", "bot2_light_if_risky"],
    "L3": ["router", "supervisor", "architect", "bot1", "tester", "bot2"],
    "L4": ["router", "supervisor", "architect", "bot1", "tester", "bot2", "devops_if_approved"],
}
PARSING_PROCESS_PLAN = ["router", "supervisor", "bot1", "bot2"]
PARSING_HUMAN_GATE_REASONS = [
    "missing_credentials",
    "captcha",
    "2fa_or_sms_code",
    "payment_or_paid_export",
    "destructive_external_write",
    "legal_or_account_policy_blocker",
]

HIGH_RISK_RE = re.compile(
    r"\b(prod|production|deploy|server|token|secret|auth|permission|database|db|schema|"
    r"docker|cron|env|payment|money|client|supplier|tender|security|api contract|price|deadline|main branch)\b|"
    r"(прод|депло|сервер|токен|секрет|баз|схем|деньг|клиент|поставщик|цен|срок|безопас|гитхаб|пуш|мердж)",
    re.I,
)
CODE_RE = re.compile(
    r"\b(code|python|script|test|pytest|refactor|bug|fix|diff|ci|pipeline|task_router\.py|\.py)\b|"
    r"(код|тест|рефак|баг|скрипт)",
    re.I,
)
ARCH_RE = re.compile(
    r"\b(architecture|architect|strategy|design|multi-agent|agent|supervisor|process|worker)\b|"
    r"(архитект|стратег|агент|процесс)",
    re.I,
)
DOC_RE = re.compile(r"\b(document|pdf|excel|report|compare|analy[sz]e|checklist|summary)\b", re.I)
SIMPLE_RE = re.compile(r"\b(translate|rewrite|short|explain|sanity|2\+2|hello)\b|(перепиш|корот|объясн)", re.I)
COMMAND_RE = re.compile(r"^(status|list|show|logs?|health|date|time|tasks?)(\s|$)|^(статус|лог|покаж|список)(\s|$)", re.I)
ADVERSARIAL_RE = re.compile(r"skip tests|bypass|without tests|without review|без тест|обойт|срочн|просто выкат", re.I)
GITHUB_CONTEXT_RE = re.compile(r"\b(github|pull request|pr|issue)\b|(гитхаб|гитхабе|гитхаба)", re.I)
GITHUB_READ_RE = re.compile(r"\b(look up|lookup|read|show|list|summari[sz]e|status|comment|inspect|find)\b|(найд|покаж|посмотр|статус)", re.I)
GIT_WRITE_RE = re.compile(r"\b(push|merge|deploy|release|tag|commit|write|delete|close|reopen|main)\b|(запуш|пуш|мердж|депло|в main)", re.I)
MIGRATION_RE = re.compile(r"\b(migration|migrate|sqlite|postgres|postgresql|schema rollback|database migration)\b|(миграц|постгрес|схем)", re.I)
MIGRATION_WRITE_RE = re.compile(
    r"\b(apply|run|execute|create|edit|write|implement|deploy|production)\b.*\b(migration|schema)\b|"
    r"(примен|запуст|выкат)",
    re.I,
)
SUPPLIER_PRICE_DEADLINE_RE = re.compile(
    r"\b(supplier|vendor|price|deadline|delivery|lead time|tender|crm parts?)\b|"
    r"(поставщик|поставщиков|цен|стоимост|срок|доставк|тендер|закупк|закупки|контур|"
    r"эксел|excel|xlsx)",
    re.I,
)
KONTUR_BROWSER_RE = re.compile(
    r"\b(kontur|zakupki(?:\.kontur\.ru)?)\b.*\b(parse|scrape|scraping|browser|search|export|evidence|capture|excel|xlsx)\b|"
    r"\b(parse|scrape|scraping|browser|search|export|evidence|capture|excel|xlsx)\b.*\b(kontur|zakupki(?:\.kontur\.ru)?)\b|"
    r"(контур|закупк).*(парс|скрап|брауз|поиск|собер|экспорт|доказ|скрин|эксел)|"
    r"(парс|скрап|брауз|поиск|собер|экспорт|доказ|скрин|эксел).*(контур|закупк)",
    re.I,
)
MARKETPLACE_BROWSER_RE = re.compile(
    r"\b(b2b-center|b2b|marketplace|external site|website)\b.*\b(parse|scrape|scraping|browser|search|export|excel|xlsx|tender|supplier)\b|"
    r"\b(parse|scrape|scraping|browser|search|export|excel|xlsx|tender|supplier)\b.*\b(b2b-center|b2b|marketplace|external site|website)\b|"
    r"(площадк|маркетплейс|внешн.{0,20}сайт|сайт).{0,80}(поиск|парс|собер|экспорт|эксел|тендер|закупк|лом|р6м5|р18)|"
    r"(поиск|парс|собер|экспорт|эксел|тендер|закупк|лом|р6м5|р18).{0,80}(площадк|маркетплейс|внешн.{0,20}сайт|сайт)",
    re.I,
)
MATERIAL_TENDER_RE = re.compile(
    r"(продаж|реализац|закупк|тендер|поиск|парс|экспорт|excel|xlsx).{0,80}(лом|р6м5|р18|д16т|быстрорежущ)|"
    r"(лом|р6м5|р18|д16т|быстрорежущ).{0,80}(продаж|реализац|закупк|тендер|поиск|парс|экспорт|excel|xlsx)",
    re.I,
)


@dataclass(frozen=True)
class Route:
    task_level: str
    task_type: str
    risk_level: str
    reason: str
    stress_profile: str
    review_required: bool
    human_gate_required: bool

    def as_dict(self) -> dict[str, Any]:
        defaults = dict(LEVEL_DEFAULTS[self.task_level])
        process_plan = PROCESS_PLAN[self.task_level]
        autonomy_policy: dict[str, Any] = {}
        if self.task_type == "supplier_price_deadline_analysis":
            process_plan = PARSING_PROCESS_PLAN
            autonomy_policy = {
                "mode": "parsing_bot1_bot2_only",
                "max_worker_agents": 2,
                "script_writer": "bot1",
                "reviewer": "bot2",
                "forbidden_roles": ["architect", "tester", "devops_if_approved", "extra_parallel_agents"],
                "ask_human_only_for": PARSING_HUMAN_GATE_REASONS,
                "default_action_on_bot2_changes": "return_to_bot1_without_human_gate",
            }
        defaults.update(
            {
                "task_level": self.task_level,
                "task_type": self.task_type,
                "risk_level": self.risk_level,
                "reason": self.reason,
                "stress_profile": self.stress_profile,
                "review_required": self.review_required,
                "human_gate_required": self.human_gate_required,
                "process_plan": process_plan,
            }
        )
        if autonomy_policy:
            defaults["autonomy_policy"] = autonomy_policy
        return defaults


def classify_task(task: str) -> dict[str, Any]:
    text = " ".join(task.strip().split())
    lower = text.lower()
    high_risk = bool(HIGH_RISK_RE.search(text))
    code = bool(CODE_RE.search(text))
    arch = bool(ARCH_RE.search(text))
    doc = bool(DOC_RE.search(text))
    simple = bool(SIMPLE_RE.search(text))
    command = bool(COMMAND_RE.search(text)) and len(text) < 80
    adversarial = bool(ADVERSARIAL_RE.search(text))
    github_context = bool(GITHUB_CONTEXT_RE.search(text))
    git_write = bool(GIT_WRITE_RE.search(text))
    github_lookup = github_context and bool(GITHUB_READ_RE.search(text)) and not git_write
    migration = bool(MIGRATION_RE.search(text))
    migration_write = migration and bool(MIGRATION_WRITE_RE.search(text))
    kontur_browser_research = bool(KONTUR_BROWSER_RE.search(text))
    marketplace_browser_research = bool(MARKETPLACE_BROWSER_RE.search(text))
    material_tender_research = bool(MATERIAL_TENDER_RE.search(text))
    supplier_price_deadline = (
        bool(SUPPLIER_PRICE_DEADLINE_RE.search(text))
        or kontur_browser_research
        or marketplace_browser_research
        or material_tender_research
    )
    long = len(text) > 450

    if command:
        level, task_type, reason = "L0", "command_or_status", "Command/status can run without LLM."
    elif github_lookup:
        level, task_type, reason = "L2", "github_lookup", "Read-only GitHub lookup is not a code change."
    elif migration_write:
        level, task_type, reason = "L4", "database_migration_change", "Migration changes require code/data gate and rollback evidence."
    elif git_write:
        level, task_type, reason = "L4", "git_write_or_deploy", "Git push/merge/deploy is an external write."
    elif migration:
        level, task_type, reason = "L3", "database_migration_plan", "Migration planning requires architecture, rollback, and review."
    elif supplier_price_deadline:
        level, task_type, reason = (
            "L2",
            "supplier_price_deadline_analysis",
            "Supplier price/deadline analysis affects money, delivery dates, and CRM purchasing decisions.",
        )
    elif code and (high_risk or "multi" in lower or "deploy" in lower or "депло" in lower):
        level, task_type, reason = "L4", "code_or_deploy_project", "High-risk code/deploy requires project pipeline."
    elif code:
        level, task_type, reason = "L4", "code_change", "Code changes require Bot#2 code gate and tests."
    elif arch or long:
        level, task_type, reason = "L3", "architecture_or_strategy", "Architecture, roles, or multi-step solution required."
    elif doc:
        level, task_type, reason = "L2", "analysis_or_checklist", "Structured analysis/checklist task."
    elif simple:
        level, task_type, reason = "L1", "simple_text_task", "Simple short text task."
    else:
        level, task_type, reason = "L2", "standard_task", "Standard task without multi-agent execution."

    high_risk = high_risk or adversarial or git_write or migration or supplier_price_deadline
    risk = "high" if high_risk else "medium" if level in {"L2", "L3", "L4"} else "low"
    review_required = level in {"L3", "L4"} or risk == "high"
    if level == "L1":
        review_required = risk == "high"
    if level == "L0":
        review_required = False
    human_gate_required = adversarial or git_write or migration_write or (
        risk == "high" and level == "L4" and any(word in lower for word in ["deploy", "production", "prod", "депло"])
    )
    stress_profile = "adversarial" if adversarial else "normal"

    return Route(level, task_type, risk, reason, stress_profile, review_required, human_gate_required).as_dict()


def _normalize_level(value: Any) -> str:
    level = str(value or "").upper()
    return level if level in LEVEL_RANK else ""


def _normalize_risk(value: Any) -> str:
    risk = str(value or "").lower()
    return risk if risk in RISK_RANK else ""


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "да", "high", "required"}
    return bool(value)


def _route_with_level(route: dict[str, Any], level: str) -> dict[str, Any]:
    updated = dict(route)
    for key, value in LEVEL_DEFAULTS[level].items():
        updated[key] = value
    updated["task_level"] = level
    updated["process_plan"] = PROCESS_PLAN[level]
    return updated


def apply_classification_audit(route: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    """Apply Bot#2 classification audit conservatively.

    Bot#2 can raise level/risk or require review/human gate. It cannot lower
    Router classification or relax gates.
    """

    updated = dict(route)
    applied: list[str] = []
    ignored: list[str] = []
    original_level = str(route.get("task_level") or "")
    original_risk = str(route.get("risk_level") or "")
    recommended_level = _normalize_level(audit.get("recommended_level") or audit.get("task_level"))
    recommended_risk = _normalize_risk(audit.get("risk_level") or audit.get("recommended_risk_level"))

    if recommended_level:
        if LEVEL_RANK[recommended_level] > LEVEL_RANK.get(original_level, -1):
            updated = _route_with_level(updated, recommended_level)
            applied.append(f"task_level:{original_level}->{recommended_level}")
        elif LEVEL_RANK[recommended_level] < LEVEL_RANK.get(original_level, -1):
            ignored.append(f"task_level:{original_level}->{recommended_level}")

    current_risk = str(updated.get("risk_level") or original_risk)
    if recommended_risk:
        if RISK_RANK[recommended_risk] > RISK_RANK.get(current_risk, -1):
            updated["risk_level"] = recommended_risk
            applied.append(f"risk_level:{current_risk}->{recommended_risk}")
        elif RISK_RANK[recommended_risk] < RISK_RANK.get(current_risk, -1):
            ignored.append(f"risk_level:{current_risk}->{recommended_risk}")

    if _truthy(audit.get("review_required")) and not bool(updated.get("review_required")):
        updated["review_required"] = True
        applied.append("review_required:false->true")
    if _truthy(audit.get("human_gate_required")) and not bool(updated.get("human_gate_required")):
        updated["human_gate_required"] = True
        applied.append("human_gate_required:false->true")

    if updated.get("task_level") in {"L3", "L4"} or updated.get("risk_level") == "high":
        if not bool(updated.get("review_required")):
            updated["review_required"] = True
            applied.append("review_required:policy->true")

    updated["classification_audit"] = {
        "source": str(audit.get("source") or "bot2"),
        "status": str(audit.get("status") or "AUDITED"),
        "summary": str(audit.get("summary") or audit.get("reason") or ""),
        "recommended_level": recommended_level,
        "recommended_risk_level": recommended_risk,
        "applied": applied,
        "ignored_demotions": ignored,
        "raw": audit,
    }
    return updated


def parse_classification_audit(raw: str) -> dict[str, Any]:
    invalid_audit = {
        "status": "INVALID_CLASSIFICATION_AUDIT",
        "risk_level": "high",
        "review_required": True,
        "summary": "Bot#2 route audit did not return a valid JSON object; require review fail-safe.",
    }
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        candidates = re.findall(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", raw, flags=re.S)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return invalid_audit
    if not isinstance(parsed, dict):
        return invalid_audit
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes Retek deterministic task router")
    parser.add_argument("--task", required=True)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    print(json.dumps(classify_task(args.task), ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
