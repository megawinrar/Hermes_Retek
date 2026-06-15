#!/usr/bin/env python3
"""g3-shaped browser session adapter for Hermes.

The script exposes a small browser action surface over Puppeteer while keeping
state in a persistent user data directory. It is intentionally not a stealth or
anti-bot layer: it automates an authenticated browser session, records evidence,
and keeps cookie values out of stdout and audit logs by default.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from secret_patterns import redact_payload
except ImportError:  # pragma: no cover - package-style import fallback
    from scripts.secret_patterns import redact_payload
try:
    import browser_pacing
except ImportError:  # pragma: no cover - package-style import fallback
    from scripts import browser_pacing


DEFAULT_ROOT = Path(os.environ.get("HERMES_BROWSER_ROOT", "/opt/data/rebrowser"))
DEFAULT_NODE_BIN = os.environ.get("HERMES_NODE_BIN", "node")
DEFAULT_TIMEOUT_MS = int(os.environ.get("HERMES_BROWSER_TIMEOUT_MS", "45000"))
DEFAULT_WAIT_UNTIL = os.environ.get("HERMES_BROWSER_WAIT_UNTIL", "networkidle2")
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ALLOWED_URL_SCHEMES = {"http", "https", "file"}


NODE_RUNNER = r"""
const fs = require('fs/promises');
const path = require('path');

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString('utf8');
}

function requireString(payload, name) {
  const value = payload[name];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`missing required string: ${name}`);
  }
  return value;
}

function summarizeCookies(cookies) {
  return cookies.map((cookie) => ({
    name: cookie.name,
    domain: cookie.domain,
    path: cookie.path,
    expires: cookie.expires,
    httpOnly: !!cookie.httpOnly,
    secure: !!cookie.secure,
    sameSite: cookie.sameSite || ''
  }));
}

async function ensureDir(filePath) {
  await fs.mkdir(path.dirname(filePath), { recursive: true });
}

async function main() {
  const payload = JSON.parse(await readStdin());
  const puppeteerPackage = process.env.HERMES_PUPPETEER_PACKAGE || 'puppeteer';
  const puppeteer = require(puppeteerPackage);
  const launchArgs = ['--no-sandbox', '--disable-dev-shm-usage'];
  const launchOptions = {
    headless: payload.headless,
    userDataDir: payload.profile_dir,
    args: launchArgs,
    defaultViewport: payload.viewport || { width: 1440, height: 1000 }
  };
  if (payload.chrome_binary) {
    launchOptions.executablePath = payload.chrome_binary;
  }

  const browser = await puppeteer.launch(launchOptions);
  const startedAt = Date.now();
  try {
    const pages = await browser.pages();
    const page = pages[0] || await browser.newPage();
    page.setDefaultTimeout(payload.timeout_ms);
    page.setDefaultNavigationTimeout(payload.timeout_ms);
    if (payload.user_agent) {
      await page.setUserAgent(payload.user_agent);
    }

    let result = {};
    if (payload.action === 'start') {
      if (payload.url) {
        await page.goto(payload.url, { waitUntil: payload.wait_until, timeout: payload.timeout_ms });
      }
      result = { url: page.url(), title: await page.title() };
    } else if (payload.action === 'goto') {
      const url = requireString(payload, 'url');
      await page.goto(url, { waitUntil: payload.wait_until, timeout: payload.timeout_ms });
      result = { url: page.url(), title: await page.title() };
    } else if (payload.action === 'title') {
      result = { title: await page.title(), url: page.url() };
    } else if (payload.action === 'current_url') {
      result = { url: page.url(), title: await page.title() };
    } else if (payload.action === 'wait_for_selector') {
      const selector = requireString(payload, 'selector');
      await page.waitForSelector(selector, { visible: !!payload.visible_selector, timeout: payload.timeout_ms });
      result = { selector, found: true, url: page.url() };
    } else if (payload.action === 'click') {
      const selector = requireString(payload, 'selector');
      await page.waitForSelector(selector, { visible: true, timeout: payload.timeout_ms });
      await page.click(selector);
      result = { selector, clicked: true, url: page.url(), title: await page.title() };
    } else if (payload.action === 'type') {
      const selector = requireString(payload, 'selector');
      const text = requireString(payload, 'text');
      await page.waitForSelector(selector, { visible: true, timeout: payload.timeout_ms });
      await page.focus(selector);
      if (payload.clear_first) {
        await page.click(selector, { clickCount: 3 });
        await page.keyboard.press('Backspace');
      }
      await page.type(selector, text, { delay: payload.type_delay_ms || 0 });
      result = { selector, typed_chars: text.length, url: page.url() };
    } else if (payload.action === 'evaluate') {
      const script = requireString(payload, 'script');
      const value = await page.evaluate((source) => {
        // The Python wrapper requires an explicit --allow-evaluate flag before
        // this action can be invoked.
        return eval(source);
      }, script);
      result = { value, url: page.url() };
    } else if (payload.action === 'source') {
      const source = await page.content();
      if (payload.source_path) {
        await ensureDir(payload.source_path);
        await fs.writeFile(payload.source_path, source, 'utf8');
        await fs.chmod(payload.source_path, 0o600);
      }
      const maxLength = payload.max_length || 4000;
      const includePreview = !payload.source_path || !!payload.include_preview;
      result = {
        chars: source.length,
        saved_to: payload.source_path || '',
        preview: includePreview ? (source.length > maxLength ? source.slice(0, maxLength) : source) : ''
      };
    } else if (payload.action === 'screenshot') {
      const screenshotPath = requireString(payload, 'screenshot_path');
      await ensureDir(screenshotPath);
      await page.screenshot({ path: screenshotPath, fullPage: !!payload.full_page });
      await fs.chmod(screenshotPath, 0o600);
      result = { screenshot_path: screenshotPath, url: page.url(), title: await page.title() };
    } else if (payload.action === 'cookies') {
      const cookies = await page.cookies();
      if (payload.cookies_path) {
        await ensureDir(payload.cookies_path);
        await fs.writeFile(payload.cookies_path, JSON.stringify(cookies, null, 2), 'utf8');
        await fs.chmod(payload.cookies_path, 0o600);
      }
      result = {
        saved_to: payload.cookies_path || '',
        count: cookies.length,
        cookies: payload.unsafe_print_values ? cookies : summarizeCookies(cookies)
      };
    } else {
      throw new Error(`unsupported action: ${payload.action}`);
    }

    result.elapsed_ms = Date.now() - startedAt;
    process.stdout.write(JSON.stringify({ ok: true, result }));
  } finally {
    if (payload.keep_open_seconds && payload.keep_open_seconds > 0) {
      await new Promise((resolve) => setTimeout(resolve, payload.keep_open_seconds * 1000));
    }
    await browser.close();
  }
}

main().catch((error) => {
  process.stdout.write(JSON.stringify({
    ok: false,
    error: String(error && error.stack ? error.stack : error)
  }));
  process.exitCode = 1;
});
"""


class BrowserSessionError(RuntimeError):
    """Raised when browser action validation or execution fails."""


@dataclass(frozen=True)
class SessionPaths:
    root: Path
    session_id: str
    session_dir: Path
    profile_dir: Path
    artifacts_dir: Path
    audit_path: Path
    state_path: Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def safe_session_id(session_id: str) -> str:
    value = session_id.strip()
    if not SESSION_RE.fullmatch(value):
        raise BrowserSessionError("session id must be 1-64 chars: letters, numbers, dot, dash, underscore")
    return value


def default_session_id() -> str:
    return f"session-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except PermissionError:
        pass


def secure_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except FileNotFoundError:
        pass
    except PermissionError:
        pass


def session_paths(session_id: str, *, root: Path | str | None = None) -> SessionPaths:
    safe_id = safe_session_id(session_id)
    root_path = Path(root or DEFAULT_ROOT)
    session_dir = root_path / "sessions" / safe_id
    paths = SessionPaths(
        root=root_path,
        session_id=safe_id,
        session_dir=session_dir,
        profile_dir=session_dir / "profile",
        artifacts_dir=session_dir / "artifacts",
        audit_path=session_dir / "audit.jsonl",
        state_path=session_dir / "state.json",
    )
    for directory in [paths.root, paths.session_dir, paths.profile_dir, paths.artifacts_dir]:
        secure_dir(directory)
    return paths


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise BrowserSessionError(f"unsupported URL scheme: {parsed.scheme or '<empty>'}")
    if parsed.scheme in {"http", "https"} and not parsed.netloc:
        raise BrowserSessionError("http/https URL must include a host")
    return url


def artifact_path(paths: SessionPaths, prefix: str, suffix: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return paths.artifacts_dir / f"{stamp}-{prefix}-{uuid.uuid4().hex[:6]}{suffix}"


def _headless_from_env() -> bool:
    raw = os.environ.get("HERMES_BROWSER_HEADLESS", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", "visible"}


def base_payload(
    *,
    action: str,
    paths: SessionPaths,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    wait_until: str = DEFAULT_WAIT_UNTIL,
    headless: bool | None = None,
    chrome_binary: str = "",
    user_agent: str = "",
    viewport_width: int = 1440,
    viewport_height: int = 1000,
) -> dict[str, Any]:
    return {
        "action": action,
        "session_id": paths.session_id,
        "profile_dir": str(paths.profile_dir),
        "artifacts_dir": str(paths.artifacts_dir),
        "timeout_ms": int(timeout_ms),
        "wait_until": wait_until,
        "headless": _headless_from_env() if headless is None else bool(headless),
        "chrome_binary": chrome_binary or os.environ.get("HERMES_CHROME_BINARY", ""),
        "user_agent": user_agent or os.environ.get("HERMES_BROWSER_USER_AGENT", ""),
        "viewport": {"width": int(viewport_width), "height": int(viewport_height)},
    }


def build_payload_from_args(args: argparse.Namespace, paths: SessionPaths) -> dict[str, Any]:
    payload = base_payload(
        action=args.action,
        paths=paths,
        timeout_ms=args.timeout_ms,
        wait_until=args.wait_until,
        headless=not args.visible,
        chrome_binary=args.chrome_binary,
        user_agent=args.user_agent,
        viewport_width=args.viewport_width,
        viewport_height=args.viewport_height,
    )
    if args.action in {"start", "goto"}:
        url = getattr(args, "url", "")
        if url:
            payload["url"] = validate_url(url)
        elif args.action == "goto":
            raise BrowserSessionError("goto requires URL")
    if args.action in {"click", "type", "wait_for_selector"}:
        payload["selector"] = args.selector
    if args.action == "type":
        payload["text"] = args.text
        payload["clear_first"] = not args.no_clear
        payload["type_delay_ms"] = args.type_delay_ms
    if args.action == "wait_for_selector":
        payload["visible_selector"] = args.visible_selector
    if args.action == "evaluate":
        if not args.allow_evaluate:
            raise BrowserSessionError("evaluate requires --allow-evaluate")
        payload["script"] = args.script
    if args.action == "source":
        payload["max_length"] = args.max_length
        if args.save:
            payload["source_path"] = str(artifact_path(paths, "source", ".html"))
        payload["include_preview"] = args.include_preview
    if args.action == "screenshot":
        payload["screenshot_path"] = args.path or str(artifact_path(paths, "screenshot", ".png"))
        payload["full_page"] = args.full_page
    if args.action == "cookies":
        payload["cookies_path"] = args.path or str(paths.session_dir / "cookies.json")
        payload["unsafe_print_values"] = args.unsafe_print_values
    if getattr(args, "keep_open_seconds", 0):
        payload["keep_open_seconds"] = max(0, int(args.keep_open_seconds))
    return payload


def record_audit(
    paths: SessionPaths,
    *,
    action: str,
    status: str,
    payload: dict[str, Any],
    result: dict[str, Any] | None = None,
    error: str = "",
) -> dict[str, Any]:
    event = {
        "id": uuid.uuid4().hex,
        "created_at": utc_now(),
        "created_at_epoch": time.time(),
        "session_id": paths.session_id,
        "action": action,
        "status": status,
        "payload": redact_payload(payload),
        "result": redact_payload(result or {}),
        "error": redact_payload(error),
    }
    with paths.audit_path.open("a", encoding="utf-8") as fh:
        fh.write(dumps(event) + "\n")
    secure_file(paths.audit_path)
    paths.state_path.write_text(
        json.dumps(
            {
                "session_id": paths.session_id,
                "updated_at": event["created_at"],
                "last_action_epoch": event["created_at_epoch"],
                "last_action": action,
                "last_status": status,
                "audit_path": str(paths.audit_path),
                "profile_dir": str(paths.profile_dir),
                "artifacts_dir": str(paths.artifacts_dir),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    secure_file(paths.state_path)
    return event


def run_node_action(
    payload: dict[str, Any],
    *,
    node_bin: str = DEFAULT_NODE_BIN,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    timeout = timeout_seconds or max(5, int(payload.get("timeout_ms", DEFAULT_TIMEOUT_MS)) // 1000 + 10)
    completed = subprocess.run(
        [node_bin, "-e", NODE_RUNNER],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    stdout = completed.stdout.strip()
    if not stdout:
        raise BrowserSessionError(f"browser runner produced no stdout: {completed.stderr.strip()}")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BrowserSessionError(f"browser runner returned non-json stdout: {stdout[:500]}") from exc
    if completed.returncode != 0 or not data.get("ok"):
        message = str(data.get("error") or completed.stderr or "browser runner failed")
        raise BrowserSessionError(message)
    result = data.get("result")
    if not isinstance(result, dict):
        raise BrowserSessionError("browser runner result must be an object")
    return result


def action_output(action: str, result: dict[str, Any], *, unsafe_print_values: bool = False) -> dict[str, Any]:
    if action == "cookies" and not unsafe_print_values:
        safe_result = dict(result)
        cookies = safe_result.get("cookies")
        if isinstance(cookies, list):
            safe_result["cookies"] = [
                {key: value for key, value in cookie.items() if key != "value"} if isinstance(cookie, dict) else cookie
                for cookie in cookies
            ]
        return safe_result
    return result


def status(paths: SessionPaths) -> dict[str, Any]:
    audit_count = 0
    last_event: dict[str, Any] | None = None
    if paths.audit_path.exists():
        for line in paths.audit_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            audit_count += 1
            try:
                last_event = json.loads(line)
            except json.JSONDecodeError:
                last_event = {"status": "invalid_audit_line"}
    return {
        "session_id": paths.session_id,
        "session_dir": str(paths.session_dir),
        "profile_dir": str(paths.profile_dir),
        "artifacts_dir": str(paths.artifacts_dir),
        "audit_path": str(paths.audit_path),
        "audit_count": audit_count,
        "last_event": last_event or {},
    }


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def cmd_status(args: argparse.Namespace) -> None:
    paths = session_paths(args.session, root=args.root)
    print(json.dumps(status(paths), ensure_ascii=False, indent=2, sort_keys=True))


def cmd_action(args: argparse.Namespace) -> None:
    paths = session_paths(args.session, root=args.root)
    payload = build_payload_from_args(args, paths)
    pace_policy = browser_pacing.policy_from_values(
        profile=getattr(args, "pace_profile", None),
        min_delay_seconds=getattr(args, "min_delay_seconds", None),
        max_delay_seconds=getattr(args, "max_delay_seconds", None),
    )
    pace_event = browser_pacing.pace_before_action(
        session_id=paths.session_id,
        state_path=paths.state_path,
        action=args.action,
        policy=pace_policy,
    )
    payload["pace"] = pace_event
    started = time.monotonic()
    try:
        result = run_node_action(payload, node_bin=args.node_bin)
        result["wall_ms"] = int((time.monotonic() - started) * 1000)
        result["pace"] = pace_event
        record_audit(paths, action=args.action, status="ok", payload=payload, result=result)
        print(
            json.dumps(
                action_output(
                    args.action,
                    result,
                    unsafe_print_values=getattr(args, "unsafe_print_values", False),
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
    except Exception as exc:
        record_audit(paths, action=args.action, status="error", payload=payload, error=str(exc))
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hermes browser session adapter")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="Browser session root directory")
    parser.add_argument("--session", default="default", help="Persistent session id")
    sub = parser.add_subparsers(dest="action", required=True)

    status_cmd = sub.add_parser("status")
    status_cmd.set_defaults(func=cmd_status)

    def add_common(action_parser: argparse.ArgumentParser) -> None:
        action_parser.add_argument("--node-bin", default=DEFAULT_NODE_BIN)
        action_parser.add_argument("--chrome-binary", default="")
        action_parser.add_argument("--timeout-ms", type=int, default=DEFAULT_TIMEOUT_MS)
        action_parser.add_argument("--wait-until", default=DEFAULT_WAIT_UNTIL)
        action_parser.add_argument("--visible", action="store_true", help="Launch a visible browser window")
        action_parser.add_argument("--user-agent", default="", help="Optional ordinary browser UA override")
        action_parser.add_argument("--viewport-width", type=int, default=1440)
        action_parser.add_argument("--viewport-height", type=int, default=1000)
        action_parser.add_argument("--keep-open-seconds", type=int, default=0)
        action_parser.add_argument(
            "--pace-profile",
            choices=["off", "human", "kontur", "cautious", "slow", "bulk"],
            default=os.environ.get("HERMES_BROWSER_PACE_PROFILE", "human"),
            help="Human-paced delay profile between browser actions in the same session.",
        )
        action_parser.add_argument("--min-delay-seconds", type=float, default=None)
        action_parser.add_argument("--max-delay-seconds", type=float, default=None)
        action_parser.set_defaults(func=cmd_action)

    start = sub.add_parser("start", help="Start browser and optionally open URL")
    start.add_argument("url", nargs="?", default="")
    add_common(start)

    goto = sub.add_parser("goto", help="Navigate to URL")
    goto.add_argument("url")
    add_common(goto)

    for action in ["title", "current_url"]:
        action_parser = sub.add_parser(action)
        add_common(action_parser)

    click = sub.add_parser("click")
    click.add_argument("selector")
    add_common(click)

    type_cmd = sub.add_parser("type")
    type_cmd.add_argument("selector")
    type_cmd.add_argument("text")
    type_cmd.add_argument("--no-clear", action="store_true")
    type_cmd.add_argument("--type-delay-ms", type=int, default=0)
    add_common(type_cmd)

    wait = sub.add_parser("wait_for_selector")
    wait.add_argument("selector")
    wait.add_argument("--visible-selector", action="store_true")
    add_common(wait)

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("script")
    evaluate.add_argument("--allow-evaluate", action="store_true")
    add_common(evaluate)

    source = sub.add_parser("source")
    source.add_argument("--save", action="store_true")
    source.add_argument("--max-length", type=int, default=4000)
    source.add_argument("--include-preview", action="store_true")
    add_common(source)

    screenshot = sub.add_parser("screenshot")
    screenshot.add_argument("--path", default="")
    screenshot.add_argument("--full-page", action="store_true")
    add_common(screenshot)

    cookies = sub.add_parser("cookies")
    cookies.add_argument("--path", default="")
    cookies.add_argument("--unsafe-print-values", action="store_true")
    add_common(cookies)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except BrowserSessionError as exc:
        parser.exit(2, f"{redact_payload(str(exc))}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
