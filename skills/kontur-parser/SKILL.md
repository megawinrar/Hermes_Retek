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

1. Check session health first by opening `https://zakupki.kontur.ru/Grid`.
2. Reuse cookies/profile before attempting login.
3. Run one query at a time:
   - `Д16Т`;
   - `реализация Р6М5`;
   - `продажа лома Р18`;
   - `отходы быстрорежущей стали`.
4. Save evidence for every meaningful step: URL, query, screenshot if useful,
   HTML source, result count, and exported files.
5. Keep requests bounded and human-paced. Do not run open-ended scraping loops.
6. If Excel export does not start, save the page HTML and record which button or
   endpoint was attempted.
7. If selectors fail, capture current HTML and write the failing selector names
   to RLM instead of retrying blindly.

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
