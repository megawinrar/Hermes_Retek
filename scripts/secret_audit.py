#!/usr/bin/env python3
"""Audit current files and git history for secret-shaped values.

The scanner intentionally reports metadata only. It never includes matched
secret text in stdout, JSON output, or returned finding dictionaries.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence

from secret_patterns import SECRET_PATTERNS

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SecretFinding:
    scope: str
    pattern: str
    path: str
    line: int
    commit: str | None = None
    fixture: bool = False


DEFAULT_SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}
GIT_GREP_LINE_RE = re.compile(r"^([^:]+):(.+):(\d+):")


def run_git(root: Path, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def is_probably_binary(data: bytes) -> bool:
    return b"\0" in data


def is_fixture_path(path: str) -> bool:
    parts = Path(path).parts
    if not parts:
        return False
    if parts[0] in {"tests", "testdata", "fixtures", "docs"}:
        return True
    if parts[0] == "skills" and Path(path).suffix.lower() in {".md", ".markdown"}:
        return True
    return "references" in parts or "examples" in parts


def line_number_for_match(text: str, start: int) -> int:
    return text.count("\n", 0, start) + 1


def scan_text(*, scope: str, path: str, text: str, commit: str | None = None) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    fixture = is_fixture_path(path)
    for secret_pattern in SECRET_PATTERNS:
        for match in secret_pattern.pattern.finditer(text):
            findings.append(
                SecretFinding(
                    scope=scope,
                    pattern=secret_pattern.name,
                    path=path,
                    line=line_number_for_match(text, match.start()),
                    commit=commit,
                    fixture=fixture,
                )
            )
    return findings


def tracked_files(root: Path, paths: Sequence[str] | None = None) -> list[str]:
    args = ["ls-files"]
    if paths:
        args.extend(["--", *paths])
    result = run_git(root, args)
    return [line for line in result.stdout.splitlines() if line]


def should_skip(path: str) -> bool:
    return any(part in DEFAULT_SKIP_DIRS for part in Path(path).parts)


def read_worktree_file(root: Path, path: str) -> str | None:
    full_path = root / path
    try:
        data = full_path.read_bytes()
    except OSError:
        return None
    if is_probably_binary(data):
        return None
    return data.decode("utf-8", errors="ignore")


def scan_current(root: Path, paths: Sequence[str] | None = None) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for path in tracked_files(root, paths):
        if should_skip(path):
            continue
        text = read_worktree_file(root, path)
        if text is None:
            continue
        findings.extend(scan_text(scope="current", path=path, text=text))
    return findings


def iter_commits(root: Path, max_commits: int | None = None) -> list[str]:
    result = run_git(root, ["rev-list", "--all"])
    commits = [line for line in result.stdout.splitlines() if line]
    if max_commits is not None:
        return commits[:max_commits]
    return commits


def git_grep_pattern(secret_pattern_name: str, python_pattern: str) -> str:
    if secret_pattern_name == "private_key":
        return r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"
    return python_pattern


def scan_history(root: Path, *, max_commits: int | None = None, paths: Sequence[str] | None = None) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for commit in iter_commits(root, max_commits=max_commits):
        short_commit = commit[:12]
        for secret_pattern in SECRET_PATTERNS:
            grep_pattern = git_grep_pattern(secret_pattern.name, secret_pattern.pattern.pattern)
            args = ["grep", "-n", "-I", "-P", "-e", grep_pattern, commit]
            if paths:
                args.extend(["--", *paths])
            result = run_git(root, args, check=False)
            if result.returncode == 1:
                continue
            if result.returncode != 0:
                raise RuntimeError(f"git grep failed for pattern {secret_pattern.name}")
            for line in result.stdout.splitlines():
                parsed = GIT_GREP_LINE_RE.match(line)
                if not parsed:
                    continue
                _, path, line_no = parsed.groups()
                if should_skip(path):
                    continue
                findings.append(
                    SecretFinding(
                        scope="history",
                        pattern=secret_pattern.name,
                        path=path,
                        line=int(line_no),
                        commit=short_commit,
                        fixture=is_fixture_path(path),
                    )
                )
    return findings


def filter_findings(findings: Iterable[SecretFinding], *, include_fixtures: bool) -> list[SecretFinding]:
    if include_fixtures:
        return list(findings)
    return [finding for finding in findings if not finding.fixture]


def dedupe_findings(findings: Iterable[SecretFinding]) -> list[SecretFinding]:
    seen: set[tuple[str, str, str, int, str | None, bool]] = set()
    deduped: list[SecretFinding] = []
    for finding in findings:
        key = (finding.scope, finding.pattern, finding.path, finding.line, finding.commit, finding.fixture)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def summarize(findings: Sequence[SecretFinding]) -> dict[str, object]:
    by_scope: dict[str, int] = {}
    by_pattern: dict[str, int] = {}
    for finding in findings:
        by_scope[finding.scope] = by_scope.get(finding.scope, 0) + 1
        by_pattern[finding.pattern] = by_pattern.get(finding.pattern, 0) + 1
    return {
        "findings": len(findings),
        "by_scope": dict(sorted(by_scope.items())),
        "by_pattern": dict(sorted(by_pattern.items())),
    }


def finding_to_safe_dict(finding: SecretFinding) -> dict[str, object]:
    return asdict(finding)


def render_text_report(findings: Sequence[SecretFinding]) -> str:
    lines = ["Secret audit report", json.dumps(summarize(findings), sort_keys=True)]
    for finding in findings:
        location = f"{finding.path}:{finding.line}"
        commit = f" commit={finding.commit}" if finding.commit else ""
        fixture = " fixture=true" if finding.fixture else ""
        lines.append(f"- {finding.scope} {finding.pattern} {location}{commit}{fixture}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan current files and git history for secret-shaped values.")
    parser.add_argument("--root", default=str(ROOT), help="Repository root. Defaults to the Hermes_Retek checkout.")
    parser.add_argument("--current", action="store_true", help="Scan current tracked worktree files.")
    parser.add_argument("--history", action="store_true", help="Scan all reachable git history.")
    parser.add_argument("--paths", nargs="*", help="Optional pathspecs passed to git.")
    parser.add_argument("--max-commits", type=int, help="Limit history scan to the newest N commits.")
    parser.add_argument("--include-fixtures", action="store_true", help="Include tests/fixtures findings in output and exit status.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a text report.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    scan_current_enabled = bool(args.current or not args.history)
    findings: list[SecretFinding] = []
    if scan_current_enabled:
        findings.extend(scan_current(root, args.paths))
    if args.history:
        findings.extend(scan_history(root, max_commits=args.max_commits, paths=args.paths))
    findings = dedupe_findings(filter_findings(findings, include_fixtures=bool(args.include_fixtures)))

    if args.json:
        print(
            json.dumps(
                {"summary": summarize(findings), "findings": [finding_to_safe_dict(finding) for finding in findings]},
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
    else:
        print(render_text_report(findings))
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
