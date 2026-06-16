# Kairos Bot Runtime Handoff

Server runtime path inside `hermes-agent`:

```bash
/opt/data/kairos-bot
```

Host path for the same Docker volume:

```bash
/var/lib/docker/volumes/hermes-data/_data/kairos-bot
```

The bot token must stay outside Git. On the server it is stored in:

```bash
/opt/data/kairos-bot/.env
```

Run polling mode from inside the container:

```bash
cd /opt/data/kairos-bot
.venv/bin/python poll_runner.py
```

What was fixed on 2026-06-16:

- removed unsupported `aiohttp.web.Application(lifespan=...)`;
- added `.env` token loading instead of hardcoded runtime token;
- added Russian HTML UI instead of mixed Markdown/HTML;
- added inline task buttons after every created task;
- added callback handling for task status changes and task details;
- fixed `/task1` command parsing;
- fixed RLM SQLite `ON CONFLICT(pattern, keyword)` bug by using a unique pattern key;
- added `deleteWebhook` before polling, because Telegram `getUpdates` does not work while webhook is active;
- created isolated server venv at `/opt/data/kairos-bot/.venv` with `aiohttp`;
- verified with `smoke_kairos.py`.

Quick verification:

```bash
cd /opt/data/kairos-bot
.venv/bin/python -m py_compile kairos_bot.py poll_runner.py
.venv/bin/python smoke_kairos.py
```
