from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from secret_patterns import SECRET_PATTERNS  # noqa: E402


def iter_scanned_files() -> list[Path]:
    files: list[Path] = []
    for folder in [ROOT / "scripts", ROOT / "configs"]:
        if folder.exists():
            files.extend(
                path
                for path in folder.rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"}
            )
    return files


def test_no_committed_secrets_in_scripts_or_configs() -> None:
    offenders: list[str] = []
    for path in iter_scanned_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(secret_pattern.pattern.search(text) for secret_pattern in SECRET_PATTERNS):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
