from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import patch_marketplace_process_guard  # noqa: E402


BASE_SNIPPET = '''from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ToolCallSignature:
    tool_name: str
    args_hash: str = "hash"

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> "ToolCallSignature":
        return cls(tool_name=tool_name)


@dataclass(frozen=True)
class ToolGuardrailDecision:
    action: str = "allow"
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        return self.action in {"block", "halt"}


def canonical_tool_args(args: Mapping[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)


def _coerce_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


class ToolCallGuardrailController:
    def __init__(self):
        self._halt_decision = None

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> ToolGuardrailDecision:
        signature = ToolCallSignature.from_call(tool_name, _coerce_args(args))
        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)
'''


def _patched_namespace(monkeypatch) -> dict[str, object]:
    monkeypatch.delenv("HERMES_RETEK_MARKETPLACE_PROCESS_GUARD", raising=False)
    monkeypatch.delenv("HERMES_PROCESS_STORE", raising=False)
    updated, changed = patch_marketplace_process_guard.patch_marketplace_process_guard(BASE_SNIPPET)
    assert changed is True
    namespace: dict[str, object] = {}
    exec(compile(updated, "<patched_tool_guardrails>", "exec"), namespace)
    return namespace


def _write_process_store(path: Path, *, status: str, task: str) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE process_runs (id TEXT, status TEXT, updated_at TEXT, task TEXT)")
    con.execute(
        "INSERT INTO process_runs VALUES (?, ?, datetime('now'), ?)",
        ("proc-test", status, task),
    )
    con.commit()
    con.close()


def test_patch_marketplace_process_guard_inserts_import_helper_and_call() -> None:
    updated, changed = patch_marketplace_process_guard.patch_marketplace_process_guard(BASE_SNIPPET)

    assert changed is True
    assert patch_marketplace_process_guard.PATCH_MARKER in updated
    assert "import os" in updated
    assert "import sqlite3" in updated
    assert "MARKETPLACE_PROCESS_FIRST_PATTERNS" in updated
    assert "MARKETPLACE_PROCESS_APPROVED_STATUSES" in updated
    assert "marketplace_process_first_required" in updated


def test_patch_marketplace_process_guard_is_idempotent() -> None:
    updated, changed = patch_marketplace_process_guard.patch_marketplace_process_guard(BASE_SNIPPET)
    second, changed_again = patch_marketplace_process_guard.patch_marketplace_process_guard(updated)

    assert changed is True
    assert changed_again is False
    assert second == updated
    assert second.count(patch_marketplace_process_guard.PATCH_MARKER) == 1


def test_marketplace_puppeteer_write_file_is_blocked(monkeypatch) -> None:
    namespace = _patched_namespace(monkeypatch)
    controller = namespace["ToolCallGuardrailController"]()

    decision = controller.before_call(
        "write_file",
        {
            "path": "/opt/data/rebrowser/b2b-scraper.js",
            "content": 'const puppeteer = require("rebrowser-puppeteer-core"); await page.goto("https://www.b2b-center.ru/market/");',
        },
    )

    assert decision.action == "block_continue"
    assert decision.code == "marketplace_process_first_required"
    assert decision.allows_execution is False
    assert controller._halt_decision is None


def test_marketplace_puppeteer_write_file_is_allowed_after_recent_approved_process(monkeypatch, tmp_path: Path) -> None:
    store = tmp_path / "process.db"
    _write_process_store(
        store,
        status="approved",
        task="Write and execute B2B-Center Puppeteer scraper script and save results for Р6М5 and Р18",
    )
    namespace = _patched_namespace(monkeypatch)
    monkeypatch.setenv("HERMES_PROCESS_STORE", str(store))
    controller = namespace["ToolCallGuardrailController"]()

    decision = controller.before_call(
        "write_file",
        {
            "path": "/opt/data/rebrowser/b2b-search.js",
            "content": 'const puppeteer = require("rebrowser-puppeteer-core"); await page.goto("https://www.b2b-center.ru/market/");',
        },
    )

    assert decision.action == "allow"
    assert decision.allows_execution is True


def test_marketplace_puppeteer_write_file_still_blocked_for_unapproved_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "process.db"
    _write_process_store(
        store,
        status="return_to_bot1",
        task="Write and execute B2B-Center Puppeteer scraper script and save results for Р6М5 and Р18",
    )
    namespace = _patched_namespace(monkeypatch)
    monkeypatch.setenv("HERMES_PROCESS_STORE", str(store))
    controller = namespace["ToolCallGuardrailController"]()

    decision = controller.before_call(
        "write_file",
        {
            "path": "/opt/data/rebrowser/b2b-search.js",
            "content": 'const puppeteer = require("rebrowser-puppeteer-core"); await page.goto("https://www.b2b-center.ru/market/");',
        },
    )

    assert decision.action == "block_continue"
    assert decision.allows_execution is False


def test_normal_write_file_is_allowed(monkeypatch) -> None:
    namespace = _patched_namespace(monkeypatch)
    controller = namespace["ToolCallGuardrailController"]()

    decision = controller.before_call("write_file", {"path": "/tmp/note.txt", "content": "hello"})

    assert decision.action == "allow"
    assert decision.allows_execution is True


def test_marketplace_guard_can_be_disabled(monkeypatch) -> None:
    namespace = _patched_namespace(monkeypatch)
    monkeypatch.setenv("HERMES_RETEK_MARKETPLACE_PROCESS_GUARD", "0")
    controller = namespace["ToolCallGuardrailController"]()

    decision = controller.before_call(
        "execute_code",
        {"code": 'await page.goto("https://zakupki.kontur.ru"); await puppeteer.launch();'},
    )

    assert decision.action == "allow"
