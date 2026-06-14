from __future__ import annotations

import argparse
import json
import shlex
import stat
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hermes_browser_session as browser  # noqa: E402


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_session_paths_are_safe_and_private(tmp_path: Path) -> None:
    paths = browser.session_paths("kontur-default", root=tmp_path)

    assert paths.session_id == "kontur-default"
    assert paths.profile_dir.exists()
    assert paths.artifacts_dir.exists()
    assert mode(paths.session_dir) == 0o700
    assert mode(paths.profile_dir) == 0o700


def test_rejects_unsafe_session_ids_and_url_schemes(tmp_path: Path) -> None:
    for session_id in ["../x", ".hidden", "bad/id", "", "x" * 65]:
        try:
            browser.session_paths(session_id, root=tmp_path)
        except browser.BrowserSessionError:
            pass
        else:
            raise AssertionError(f"accepted unsafe session id: {session_id!r}")

    for url in ["javascript:alert(1)", "data:text/plain,x", "https:///missing-host"]:
        try:
            browser.validate_url(url)
        except browser.BrowserSessionError:
            pass
        else:
            raise AssertionError(f"accepted unsafe URL: {url!r}")


def test_record_audit_redacts_secret_like_values(tmp_path: Path) -> None:
    paths = browser.session_paths("kontur", root=tmp_path)
    secret = "Authorization: Bearer " + "a" * 30

    browser.record_audit(
        paths,
        action="goto",
        status="ok",
        payload={"headers": {"Authorization": f"Bearer {'a' * 30}"}},
        result={"text": secret},
    )

    raw = paths.audit_path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "[REDACTED]" in raw
    assert mode(paths.audit_path) == 0o600


def test_build_payload_for_cookies_saves_values_but_does_not_print_by_default(tmp_path: Path) -> None:
    paths = browser.session_paths("kontur", root=tmp_path)
    args = argparse.Namespace(
        action="cookies",
        timeout_ms=1000,
        wait_until="networkidle2",
        visible=False,
        chrome_binary="",
        user_agent="",
        viewport_width=1000,
        viewport_height=800,
        path="",
        unsafe_print_values=False,
        keep_open_seconds=0,
    )

    payload = browser.build_payload_from_args(args, paths)

    assert payload["cookies_path"] == str(paths.session_dir / "cookies.json")
    assert payload["unsafe_print_values"] is False


def test_cookie_action_stdout_summarizes_values_unless_unsafe(monkeypatch, tmp_path: Path, capsys) -> None:
    secret = "tok_" + "G" * 32

    def fake_run_node_action(_payload: dict[str, object], *, node_bin: str = "node") -> dict[str, object]:
        return {
            "saved_to": str(tmp_path / "cookies.json"),
            "count": 1,
            "cookies": [
                {
                    "name": "auth.sid",
                    "domain": "zakupki.kontur.ru",
                    "path": "/",
                    "value": secret,
                    "httpOnly": True,
                }
            ],
        }

    monkeypatch.setattr(browser, "run_node_action", fake_run_node_action)
    args = argparse.Namespace(
        root=tmp_path,
        session="kontur",
        action="cookies",
        timeout_ms=1000,
        wait_until="networkidle2",
        visible=False,
        chrome_binary="",
        user_agent="",
        viewport_width=1000,
        viewport_height=800,
        path="",
        unsafe_print_values=False,
        keep_open_seconds=0,
        node_bin="node-test",
    )

    browser.cmd_action(args)
    output = json.loads(capsys.readouterr().out)

    assert output["cookies"][0]["name"] == "auth.sid"
    assert "value" not in output["cookies"][0]
    assert secret not in json.dumps(output)

    assert browser.action_output(
        "cookies",
        {"cookies": [{"name": "auth.sid", "value": secret}]},
        unsafe_print_values=True,
    )["cookies"][0]["value"] == secret


def test_browser_cli_redacts_runner_errors(monkeypatch, tmp_path: Path, capsys) -> None:
    secret = "tok_" + "H" * 32

    def fake_run_node_action(_payload: dict[str, object], *, node_bin: str = "node") -> dict[str, object]:
        raise browser.BrowserSessionError(f"runner failed with {secret}")

    monkeypatch.setattr(browser, "run_node_action", fake_run_node_action)

    try:
        browser.main(["--root", str(tmp_path), "--session", "kontur", "title"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("browser CLI did not exit on BrowserSessionError")

    stderr = capsys.readouterr().err
    assert secret not in stderr
    assert "[REDACTED]" in stderr


def test_hermes_browser_skill_doc_examples_parse_against_cli() -> None:
    skill_doc = (ROOT / "skills" / "hermes-browser" / "SKILL.md").read_text(encoding="utf-8")
    commands = [
        line.strip()
        for line in skill_doc.splitlines()
        if line.strip().startswith("python scripts/hermes_browser_session.py")
    ]
    assert commands

    parser = browser.build_parser()
    for command in commands:
        argv = shlex.split(command)[2:]
        args = parser.parse_args(argv)
        assert args.session == "kontur-default"
        assert callable(args.func)


def test_evaluate_requires_explicit_allow_flag(tmp_path: Path) -> None:
    paths = browser.session_paths("kontur", root=tmp_path)
    args = argparse.Namespace(
        action="evaluate",
        timeout_ms=1000,
        wait_until="networkidle2",
        visible=False,
        chrome_binary="",
        user_agent="",
        viewport_width=1000,
        viewport_height=800,
        script="document.title",
        allow_evaluate=False,
        keep_open_seconds=0,
    )

    try:
        browser.build_payload_from_args(args, paths)
    except browser.BrowserSessionError as exc:
        assert "--allow-evaluate" in str(exc)
    else:
        raise AssertionError("evaluate was allowed without --allow-evaluate")


def test_source_save_suppresses_preview_unless_explicit(tmp_path: Path) -> None:
    paths = browser.session_paths("kontur", root=tmp_path)
    args = argparse.Namespace(
        action="source",
        timeout_ms=1000,
        wait_until="networkidle2",
        visible=False,
        chrome_binary="",
        user_agent="",
        viewport_width=1000,
        viewport_height=800,
        save=True,
        max_length=100,
        include_preview=False,
        keep_open_seconds=0,
    )

    payload = browser.build_payload_from_args(args, paths)

    assert payload["source_path"].endswith(".html")
    assert payload["include_preview"] is False


def test_run_node_action_uses_json_payload_and_returns_result(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(cmd, *, input, text, stdout, stderr, timeout, check):  # noqa: ANN001
        calls.append(
            {
                "cmd": cmd,
                "input": json.loads(input),
                "text": text,
                "timeout": timeout,
                "check": check,
            }
        )
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"ok": True, "result": {"title": "ok"}}), stderr="")

    monkeypatch.setattr(browser.subprocess, "run", fake_run)

    result = browser.run_node_action({"action": "title", "timeout_ms": 1000}, node_bin="node-test")

    assert result == {"title": "ok"}
    assert calls[0]["cmd"][:2] == ["node-test", "-e"]
    assert calls[0]["input"] == {"action": "title", "timeout_ms": 1000}
