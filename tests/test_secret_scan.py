from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERNS = [
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"Authorization:\s*Bearer\s+[A-Za-z0-9_.-]{20,}"),
    re.compile(r"(?:API_KEY|CORRECT_KEY)\s*=\s*['\"][A-Za-z0-9_.-]{20,}['\"]"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
]


def iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    for folder in [ROOT / "scripts", ROOT / "configs"]:
        if folder.exists():
            files.extend(path for path in folder.rglob("*") if path.is_file())
    return files


def test_no_committed_secrets_in_scripts_or_configs() -> None:
    offenders: list[str] = []
    for path in iter_scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
