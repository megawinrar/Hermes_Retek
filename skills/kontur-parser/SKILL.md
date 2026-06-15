---
name: kontur-parser
version: 1.0.0
description: Kontur Zakupki authenticated search, export evidence, and RLM lesson capture.
---
# Kontur Parser

Use this skill when Hermes needs supplier or tender data from
`zakupki.kontur.ru` through the user's own authorized account.

This skill is a thin domain layer over `hermes-browser`. It keeps the browser
mechanics small and moves Kontur-specific lessons into one place.

## Runtime State

Use the existing mounted session directory:

- `/opt/data/rebrowser/cookies.json` - current Kontur cookies, mode `0600`;
- `/opt/data/rebrowser/session-state.json` - structured browser/session state;
- `/opt/data/rebrowser/STATE.md` - human-readable session notes;
- `/opt/data/rebrowser/search-kontur.js` - single-search Puppeteer helper;
- `/opt/data/rebrowser/search-batches.js` - date-batch search helper;
- `/opt/data/rebrowser/login-kontur.js` - login helper that must use cookies
  first and environment credentials only if explicitly configured;
- `/opt/data/rebrowser/results-*.html` and `results-*.txt` - evidence output;
- `/opt/data/reports/` - timing/watch logs.

Never hard-code or print passwords, cookie values, auth tokens, `auth.sid`, or
`.AspNetCore.Cookies`. Scripts may read `KONTUR_EMAIL` and `KONTUR_PASSWORD`
from the environment, but normal operation should rely on the stored cookies.

## Search Workflow

0. For Telegram/user-facing tasks, start a supervised process with
   `hermes_process(action="run", ...)` before browser work so Bot#1/Bot#2,
   RLM, bounded budgets, and process logs are created.
1. Check session health first by opening `https://zakupki.kontur.ru/Grid`.
2. Reuse cookies/profile before attempting login.
2a. Confirm the generic site policy:
   `python scripts/web_parsing_policy.py --url https://zakupki.kontur.ru/Grid --task "$TASK"`.
   Kontur should resolve to `ui_seed_then_api_pagination`, one parallel request,
   and `pace_profile=kontur`.
3. Create the valid search through the UI: enter keywords, click `Найти`, and
   read the `searchId` from the resulting URL. Do not treat API `queryId` as a
   browser `searchId`; expired-link pages mean the URL/searchId path is wrong.
   Use `python scripts/kontur_search_strategy.py --keywords "реализация Р6М5" --url "$CURRENT_URL"`
   to verify whether the next step should be API pagination or UI restart.
4. Run one query at a time:
   - `Д16Т`;
   - `реализация Р6М5`;
   - `продажа лома Р18`;
   - `отходы быстрорежущей стали`.
5. Save evidence for every meaningful step: URL, query, screenshot if useful,
   HTML source, result count, and exported files.
6. Keep requests bounded and human-paced. Use `--pace-profile kontur` for
   browser actions and keep 1.25-2.5s spacing between search/filter/export
   operations. Do not launch many fetches at once; stop, checkpoint, and resume
   later if the site refuses or slows down.
7. For Excel exports, first try the UI export for the current result set. If the
   site shows a limit/error such as 2000 rows, capture screenshot + error text,
   split the failed window by dates, and retry:
   - first by 2-year windows;
   - if a 2-year window still fails, split that window into 1-year windows;
   - merge downloaded yearly Excel files after all chunks finish.
   Use `python scripts/kontur_export_strategy.py --date-from 01.01.2020 --date-to 15.06.2026`
   to generate the chunk plan.
8. If Excel export does not start, save the page HTML and record which button or
   endpoint was attempted.
9. If selectors fail, capture current HTML and write the failing selector names
   to RLM instead of retrying blindly.
10. Normalize raw `/api/grid` responses with
   `python scripts/kontur_api_normalizer.py raw.json --output data.json` instead
   of asking the LLM to infer fields from large JSON blobs.

## RLM Lessons

After each Kontur task, write compact lessons to `/opt/data/rlm_store.db`:

- working selectors, URLs, and query parameters;
- failing selectors or export paths;
- result counts and file paths;
- whether cookies were valid;
- which scripts produced useful artifacts.

Do not write raw secrets to RLM. Use secret names only, for example
`auth.sid present`, not the cookie value.

## Failure Handling

- If login is required and credentials are not configured, stop at the login
  page and preserve the browser state.
- If Kontur returns an error page, capture HTML/source and record the URL,
  status, and query.
- If BotHub or browser automation times out, keep partial artifacts and write a
  short RLM note so the next run starts from the latest known state.
