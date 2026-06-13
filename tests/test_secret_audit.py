from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import secret_audit  # noqa: E402


def test_scan_text_reports_metadata_without_secret_value() -> None:
    secret = "github_pat_" + "A" * 30
    findings = secret_audit.scan_text(scope="unit", path="configs/example.env", text=f"GITHUB_TOKEN={secret}\n")

    assert len(findings) == 2
    safe_json = json.dumps([secret_audit.finding_to_safe_dict(finding) for finding in findings])
    assert secret not in safe_json
    assert "configs/example.env" in safe_json
    assert "github_pat" in safe_json
    assert "secret_assignment" in safe_json


def test_fixture_findings_are_filterable() -> None:
    secret = "ghp_" + "B" * 30
    findings = secret_audit.scan_text(scope="unit", path="tests/test_fixture.py", text=f"token = '{secret}'\n")

    assert findings
    assert all(finding.fixture for finding in findings)
    assert secret_audit.filter_findings(findings, include_fixtures=False) == []


def test_docs_and_skill_references_are_fixture_scope() -> None:
    secret = "Authorization: Bearer " + "D" * 30
    paths = [
        "docs/example.md",
        "skills/autonomous-ai-agents/hermes-agent/references/native-mcp.md",
        "skills/creative/comfyui/SKILL.md",
    ]

    for path in paths:
        findings = secret_audit.scan_text(scope="unit", path=path, text=secret)
        assert findings
        assert all(finding.fixture for finding in findings)


def test_shell_variable_assignment_is_not_reported_as_secret_value() -> None:
    findings = secret_audit.scan_text(
        scope="unit",
        path="scripts/hermes-config-guard.sh",
        text='sed -i "s||api_key: $CORRECT_KEY|" "$CONFIG_PATH"\n',
    )

    assert findings == []


def test_shared_patterns_cover_runtime_redaction_cases() -> None:
    samples = {
        "telegram_bot_token": "123456789:" + "T" * 35,
        "bearer_header": "authorization: bearer " + "D" * 30,
        "secret_assignment": "password: " + "E" * 30,
        "private_key": "-----BEGIN PRIVATE KEY-----\n" + "F" * 40 + "\n-----END PRIVATE KEY-----",
    }

    for expected_pattern, text in samples.items():
        findings = secret_audit.scan_text(scope="unit", path="configs/runtime.yaml", text=text)
        assert any(finding.pattern == expected_pattern for finding in findings)


def test_cli_json_does_not_emit_secret_value(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    (tmp_path / "scripts").mkdir()
    secret = "Authorization: Bearer " + "C" * 30
    audited = tmp_path / "scripts" / "with_secret.sh"
    audited.write_text(secret + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "fixture"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "secret_audit.py"), "--root", str(tmp_path), "--current", "--json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 1
    assert "bearer_header" in result.stdout
    assert "scripts/with_secret.sh" in result.stdout
    assert "C" * 30 not in result.stdout
    assert secret not in result.stdout


def test_cli_history_json_does_not_emit_secret_value(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    (tmp_path / "scripts").mkdir()
    secret = "API_KEY=" + "H" * 30
    audited = tmp_path / "scripts" / "rotated.sh"
    audited.write_text(secret + "\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "leaked"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    audited.write_text("API_KEY_FILE=/run/secrets/key\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "rotated"],
        cwd=tmp_path,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "secret_audit.py"), "--root", str(tmp_path), "--history", "--json"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert result.returncode == 1
    assert "secret_assignment" in result.stdout
    assert "scripts/rotated.sh" in result.stdout
    assert "H" * 30 not in result.stdout
    assert secret not in result.stdout
