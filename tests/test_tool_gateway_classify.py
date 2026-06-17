"""Characterization tests for tool_gateway command classification.

Pin CURRENT behavior of the danger classifier and the risk->resource mapping
before any refactor. These are pure (no DB) and must stay green across the
Phase 1-5 refactors of scripts/tool_gateway.py.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from tool_gateway import classify_command, resources_for_risks  # noqa: E402


def _argv(command: str) -> list[str]:
    return shlex.split(command)


@pytest.mark.parametrize(
    "command, expected_risks",
    [
        ("git push origin main", ["git_push"]),
        ("git merge feature", ["git_merge"]),
        ("git tag v1.2.3", ["git_release"]),
        ("docker restart hermes", ["docker_restart"]),
        ("docker compose up -d", ["docker_compose_runtime_change"]),
        ("kubectl delete pod hermes-0", ["kubernetes_runtime_change"]),
        ("sqlite3 store.db 'DELETE FROM tasks'", ["sqlite_write"]),
        ("ls -la", []),
        ("cat README.md", []),
        ("git status", []),
    ],
)
def test_classify_command_risks(command: str, expected_risks: list[str]) -> None:
    result = classify_command(_argv(command))
    assert result["risks"] == sorted(expected_risks)
    assert result["dangerous"] is bool(expected_risks)


def test_deploy_keyword_marks_deploy_release() -> None:
    result = classify_command(_argv("bash deploy_prod.sh --now"))
    assert "deploy_release" in result["risks"]
    assert result["dangerous"] is True


def test_secret_write_detected_for_redirect() -> None:
    result = classify_command(_argv("bash -c 'echo token=abcdefgh12345678 > out.txt'"))
    assert "secret_write" in result["risks"]
    assert result["dangerous"] is True


def test_sudo_and_env_prefixes_are_stripped() -> None:
    # effective_argv() must see through sudo / env VAR=1 to the real command.
    result = classify_command(_argv("sudo env FOO=1 git push origin main"))
    assert result["risks"] == ["git_push"]


def test_empty_command_is_not_dangerous() -> None:
    assert classify_command([]) == {"dangerous": False, "risks": [], "command": ""}


def test_command_field_is_redacted() -> None:
    # Redaction (secret_patterns) needs a 20+ char value; the secret_write
    # classifier triggers at 8+, so only long secrets are scrubbed from the echo.
    secret = "abcdefghijklmnop12345678"
    result = classify_command(_argv(f"bash -c 'echo token={secret} > out.txt'"))
    assert secret not in result["command"]
    assert "[REDACTED]" in result["command"]


@pytest.mark.parametrize(
    "risks, expected",
    [
        (["git_push"], ["git-write"]),
        (["git_merge", "git_release"], ["git-write"]),
        (["deploy_release"], ["runtime-deploy"]),
        (["docker_restart"], ["runtime-deploy"]),
        (["docker_compose_runtime_change"], ["runtime-deploy"]),
        (["kubernetes_runtime_change"], ["runtime-deploy"]),
        (["sqlite_write"], ["database-write"]),
        (["secret_write"], ["protected-config-write"]),
        (["protected_config_or_domain_write"], ["protected-config-write"]),
        (["git_push", "sqlite_write"], ["database-write", "git-write"]),
        ([], []),
    ],
)
def test_resources_for_risks(risks: list[str], expected: list[str]) -> None:
    assert resources_for_risks(risks) == expected
