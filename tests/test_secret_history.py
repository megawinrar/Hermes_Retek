"""Characterization test for secret_audit.scan_history.

A private key committed in the past must remain detectable via the bounded
git-grep history scan, and findings must never carry the secret text.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402

from secret_audit import scan_history  # noqa: E402


PRIVATE_KEY_BLOCK = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAA\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    )


def _git_grep_pcre_supported(root: Path) -> bool:
    probe = subprocess.run(
        ["git", "grep", "-P", "-e", "x", "HEAD"], cwd=root, text=True, capture_output=True
    )
    # returncode 0 (found) or 1 (not found) means -P is supported; other => unsupported.
    return probe.returncode in {0, 1}


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "tester")
    (tmp_path / "deploy_key").write_text(PRIVATE_KEY_BLOCK)
    _git(tmp_path, "add", "deploy_key")
    _git(tmp_path, "commit", "-q", "-m", "accidental key")
    # Remove it in a later commit so it lives only in history.
    (tmp_path / "deploy_key").unlink()
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "remove key")
    return tmp_path


def test_private_key_in_history_is_detected(repo: Path) -> None:
    if not _git_grep_pcre_supported(repo):
        pytest.skip("git grep -P (PCRE) unsupported in this environment")
    findings = scan_history(repo)
    private_key_findings = [f for f in findings if f.pattern == "private_key"]
    assert private_key_findings, "expected the committed private key to be found in history"
    finding = private_key_findings[0]
    assert finding.scope == "history"
    assert finding.path == "deploy_key"
    assert finding.commit  # short commit hash recorded
    # Metadata only: no secret text leaks into the finding.
    assert "PRIVATE KEY" not in repr(finding)


def test_clean_history_has_no_findings(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "tester")
    (tmp_path / "readme.txt").write_text("nothing secret here\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "clean")
    if not _git_grep_pcre_supported(tmp_path):
        pytest.skip("git grep -P (PCRE) unsupported in this environment")
    assert scan_history(tmp_path) == []
