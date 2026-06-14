---
name: hermes-browser
version: 1.0.0
description: Authenticated browser automation for supplier research, evidence capture, and session reuse.
---
# Hermes Browser

Use this skill when Hermes must work with a website through the user's own
authorized account: open pages, search, click, type, export files, capture
evidence, and reuse an existing browser profile.

This skill follows the useful browser shape from g3:

- one persistent session id;
- browser profile stored on disk;
- small actions: start, goto, click, type, wait, source, screenshot, cookies;
- artifacts saved next to the session;
- audit log for every action;
- no cookie values or passwords in normal stdout/audit output.

## Script

Use:

```bash
python scripts/hermes_browser_session.py --session kontur-default status
python scripts/hermes_browser_session.py --session kontur-default goto https://zakupki.kontur.ru
python scripts/hermes_browser_session.py --session kontur-default click 'button[type="submit"]'
python scripts/hermes_browser_session.py --session kontur-default type 'input[name="q"]' 'реализация Р6М5'
python scripts/hermes_browser_session.py --session kontur-default source --save
python scripts/hermes_browser_session.py --session kontur-default screenshot --full-page
python scripts/hermes_browser_session.py --session kontur-default cookies
```

Default session root is `${HERMES_BROWSER_ROOT:-/opt/data/rebrowser}`.
Each session stores:

- `sessions/<id>/profile/` - persistent browser profile;
- `sessions/<id>/artifacts/` - HTML/screenshots;
- `sessions/<id>/cookies.json` - cookie export with file mode `0600`;
- `sessions/<id>/audit.jsonl` - redacted action log;
- `sessions/<id>/state.json` - latest action/state summary.

## Authenticated Research Rules

1. Use the user's authorized account only for the task they requested.
2. Prefer visible browser mode for login or fragile flows:
   `--visible --keep-open-seconds 60`.
3. Save evidence after meaningful steps: screenshot plus source when useful.
4. Keep query rate human-paced. Do not run unbounded loops.
5. Do not print cookie values. The `cookies` action writes values to a `0600`
   file and returns only names/domains unless `--unsafe-print-values` is used
   intentionally for a local debugging step.
6. Prefer `source --save` for authenticated pages. Use `--include-preview` only
   when a short stdout HTML preview is actually needed.
7. Do not add stealth plugins, CAPTCHA solving, or fingerprint spoofing. Normal
   browser configuration, persistent profile, and an ordinary user-agent override
   are allowed for compatibility.

## Kontur Pattern

For supplier/tender searches:

1. Start from the existing `kontur-default` session when available.
2. Navigate to the search page.
3. Run one search phrase at a time.
4. Save screenshot/source after results load.
5. Download/export files through the page controls.
6. Write a compact evidence note: query, URL, artifact paths, downloaded files,
   and any selectors that worked.
7. Persist the lesson to RLM after the task: working selectors, failing
   selectors, source URLs, and export quirks.

## Failure Handling

- If login is required, stop at the login page in visible mode and preserve the
  session profile after the user completes login.
- If selectors changed, capture source and screenshot, then update the selector
  notes in RLM.
- If a download/export fails, record the action, page URL, screenshot, and
  console-visible error text if available.
