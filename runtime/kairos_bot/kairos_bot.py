#!/usr/bin/env python3
"""@Kairos_Rbot: group-isolated task helper with shared pattern memory."""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web


BASE_DIR = Path(os.environ.get("KAIROS_BASE_DIR", "/opt/data/kairos-bot"))
DBS_DIR = BASE_DIR / "dbs"
ENV_PATH = BASE_DIR / ".env"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env_file(ENV_PATH)
DBS_DIR.mkdir(parents=True, exist_ok=True)

TOKEN = os.environ.get("KAIROS_TOKEN", "")
WEBHOOK_HOST = os.environ.get("KAIROS_HOST", "89.169.142.160")
WEBHOOK_PORT = int(os.environ.get("KAIROS_PORT", "8443"))
WEBHOOK_URL = os.environ.get("KAIROS_WEBHOOK_URL", f"https://{WEBHOOK_HOST}:{WEBHOOK_PORT}/webhook")
API_BASE = f"https://api.telegram.org/bot{TOKEN}"

LOG_PATH = BASE_DIR / "kairos_bot.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [KAIROS] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
log = logging.getLogger("kairos")

_conns: dict[Any, sqlite3.Connection] = {}

TASK_TYPE_LABELS = {
    "production": "Производственная задача",
    "drawing": "Чертёж / изделие",
    "material": "Материал / сырьё",
    "quantity_query": "Потребность / количество",
    "confirmed": "Подтверждённая задача",
    "unknown": "Задача",
}
STATUS_LABELS = {
    "pending": "⏳ Ожидает разбора",
    "in_progress": "🔄 В работе",
    "waiting_price": "💰 Ждём цену / поставщика",
    "done": "✅ Выполнено",
    "cancelled": "❌ Не задача",
}

DRAWING_PATTERNS = [
    r"черт[её]ж",
    r"черт\.\s*\d+",
    r"дет[аа]ль",
    r"издели[ея]\s*№",
    r"поршень",
    r"корпус",
    r"втулк[аи]",
    r"бо[её]к",
    r"шайб[аы]",
    r"кольц[оа]",
    r"гайк[аи]",
    r"шпильк[аи]",
    r"направляющ",
    r"диск\s*черт",
    r"ударник",
]
QUANTITY_PATTERNS = [
    r"(\d[\d\s]*)\s*(шт|штук|компл|тыс)",
    r"потребность\s*(?:в|на)?\s*\d",
    r"нужно\s*\d",
    r"изготовить\s*\d",
    r"объ[её]м\s*(?:производств|выпуск)",
]
PRICE_PATTERNS = [r"(\d[\d\s]*)\s*(руб|р\.)", r"цен[аы]", r"стоимость", r"поставщик", r"бюджет"]
MATERIAL_PATTERNS = [
    r"\bД16Т\b",
    r"\bА12\b",
    r"\bР6М5\b",
    r"\bР18\b",
    r"\b9ХС\b",
    r"\bХВГ\b",
    r"\bХ12МФ\b",
    r"\bВК8\b",
    r"стал[ьи]\s*калиброван",
    r"пруток",
    r"лент[аы]",
    r"капсюл[ья]",
    r"детонатор",
    r"взрывчат",
]


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def now_text() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def get_db(chat_id: int) -> sqlite3.Connection:
    path = str(DBS_DIR / f"{chat_id}.db")
    if chat_id not in _conns:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=3000")
        _init_schema(conn)
        _conns[chat_id] = conn
    return _conns[chat_id]


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id INTEGER,
            user_id INTEGER,
            text TEXT,
            type TEXT DEFAULT 'text',
            is_task INTEGER DEFAULT 0,
            task_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'pending',
            quantity TEXT,
            price TEXT,
            supplier TEXT,
            drawing_ref TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS context (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def get_rlm() -> sqlite3.Connection:
    path = str(BASE_DIR / "kairos_rlm.db")
    if "_rlm" not in _conns:
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT UNIQUE,
                keyword TEXT,
                source TEXT DEFAULT 'group',
                count INTEGER DEFAULT 1,
                success_rate REAL DEFAULT 0.0,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS plants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                okved TEXT,
                keywords TEXT,
                match_count INTEGER DEFAULT 0
            );
            """
        )
        conn.commit()
        _conns["_rlm"] = conn
    return _conns["_rlm"]


def detect_patterns(text: str) -> dict[str, Any]:
    text = text or ""
    lower = text.lower()
    pats: dict[str, Any] = {"drawing": False, "quantity": False, "price": False, "material": False, "keywords": []}
    for pattern in DRAWING_PATTERNS:
        if re.search(pattern, lower):
            pats["drawing"] = True
            pats["keywords"].append(f"чертёж:{pattern}")
    for pattern in QUANTITY_PATTERNS:
        match = re.search(pattern, lower)
        if match:
            pats["quantity"] = True
            pats["keywords"].append(f"кол-во:{match.group(0)[:30]}")
    for pattern in PRICE_PATTERNS:
        if re.search(pattern, lower):
            pats["price"] = True
            pats["keywords"].append(f"цена:{pattern}")
    for pattern in MATERIAL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            pats["material"] = True
            pats["keywords"].append(f"материал:{pattern}")
    return pats


def determine_task_type(patterns: dict[str, Any]) -> str:
    if patterns.get("drawing") and patterns.get("quantity"):
        return "production"
    if patterns.get("drawing"):
        return "drawing"
    if patterns.get("material"):
        return "material"
    if patterns.get("quantity"):
        return "quantity_query"
    return "unknown"


async def api_call(method: str, **kwargs: Any) -> dict[str, Any]:
    if not TOKEN:
        raise RuntimeError("KAIROS_TOKEN is not configured")
    async with aiohttp.ClientSession() as sess:
        async with sess.post(f"{API_BASE}/{method}", json=kwargs, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            data = await resp.json(content_type=None)
            if not data.get("ok"):
                log.warning("Telegram API %s returned: %s", method, data)
            return data


async def send_rich(chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return await api_call("sendMessage", **payload)


async def send_with_keyboard(chat_id: int, text: str, buttons: list[list[dict[str, str]]]) -> dict[str, Any]:
    return await send_rich(chat_id, text, reply_markup={"inline_keyboard": buttons})


def task_keyboard(task_id: int) -> list[list[dict[str, str]]]:
    return [
        [
            {"text": "🔄 В работе", "callback_data": f"status_{task_id}_in_progress"},
            {"text": "💰 Ждём цену", "callback_data": f"status_{task_id}_waiting_price"},
        ],
        [
            {"text": "✅ Выполнено", "callback_data": f"status_{task_id}_done"},
            {"text": "❌ Не задача", "callback_data": f"status_{task_id}_cancelled"},
        ],
        [{"text": "📋 Детали", "callback_data": f"show_{task_id}"}],
    ]


async def process_message(chat_id: int, msg: dict[str, Any], db: sqlite3.Connection) -> Any:
    text = msg.get("text") or msg.get("caption") or ""
    msg_id = int(msg.get("message_id") or 0)
    user_id = int((msg.get("from") or {}).get("id") or 0)
    has_media = bool(msg.get("document") or msg.get("photo"))

    db.execute(
        "INSERT INTO messages (msg_id, user_id, text, type) VALUES (?,?,?,?)",
        (msg_id, user_id, text, "media" if has_media else "text"),
    )
    db.commit()

    if text.startswith("/"):
        return await handle_command(chat_id, text, db)

    pats = detect_patterns(text)
    if has_media:
        pats["drawing"] = True
        if not pats["keywords"]:
            pats["keywords"].append("media:drawing")
    task_type = determine_task_type(pats)
    is_task = bool(pats["drawing"] or pats["quantity"] or pats["price"] or pats["material"])

    if not is_task and has_media:
        return await ask_confirm_task(chat_id, msg_id)
    if is_task:
        db.execute("UPDATE messages SET is_task=1 WHERE msg_id=?", (msg_id,))
        db.commit()
        return await handle_task(chat_id, text, pats, task_type, msg_id, db, has_media)
    return None


async def ask_confirm_task(chat_id: int, msg_id: int) -> dict[str, Any]:
    return await send_with_keyboard(
        chat_id,
        "<b>Это задача?</b>\n\nЕсли это чертёж, заказ или потребность — сохраню как задачу. "
        "Если это просто обсуждение — оставлю как сообщение.",
        [[{"text": "✅ Да, задача", "callback_data": f"task_yes_{msg_id}"}, {"text": "❌ Нет", "callback_data": f"task_no_{msg_id}"}]],
    )


def save_patterns(task_type: str, keywords: list[str]) -> None:
    if not keywords:
        return
    rlm = get_rlm()
    for keyword in keywords:
        unique_pattern = f"{task_type}:{keyword}"
        rlm.execute(
            """
            INSERT INTO patterns (pattern, keyword, count, updated_at)
            VALUES (?,?,1,CURRENT_TIMESTAMP)
            ON CONFLICT(pattern) DO UPDATE SET
                keyword=excluded.keyword,
                count=count+1,
                updated_at=CURRENT_TIMESTAMP
            """,
            (unique_pattern, keyword),
        )
    rlm.commit()


def similar_patterns(keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    rlm = get_rlm()
    found: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        needle = keyword.split(":")[-1][:10]
        rows = rlm.execute(
            "SELECT DISTINCT keyword, count FROM patterns WHERE keyword LIKE ? ORDER BY count DESC LIMIT 5",
            (f"%{needle}%",),
        ).fetchall()
        for row in rows:
            value = str(row["keyword"])
            if value not in seen:
                seen.add(value)
                found.append(value)
    return found[:5]


async def handle_task(
    chat_id: int,
    text: str,
    pats: dict[str, Any],
    task_type: str,
    msg_id: int,
    db: sqlite3.Connection,
    has_media: bool,
) -> dict[str, Any]:
    label = TASK_TYPE_LABELS.get(task_type, "Задача")
    now = now_text()
    db.execute(
        """INSERT INTO tasks (title, description, status, created_at, updated_at)
           VALUES (?,?,?,?,?)""",
        (label, text[:1000], "pending", now, now),
    )
    db.commit()
    task_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])
    db.execute("UPDATE messages SET task_id=? WHERE msg_id=?", (task_id, msg_id))
    db.commit()

    save_patterns(task_type, list(pats.get("keywords") or []))
    similar = similar_patterns(list(pats.get("keywords") or []))

    details = []
    if pats.get("drawing"):
        details.append("🎨 Чертёж / изделие")
    if pats.get("quantity"):
        details.append("🔢 Количество указано")
    if pats.get("price"):
        details.append("💰 Цена / поставщик")
    if pats.get("material"):
        details.append("🔩 Материал")
    if has_media:
        details.append("📎 Есть вложение")

    lines = [
        f"<b>📋 Задача #{task_id}</b>",
        f"<b>Тип:</b> {esc(label)}",
        f"<b>Статус:</b> {STATUS_LABELS['pending']}",
        "",
        "<b>Что распознано:</b>",
        *(f"• {esc(item)}" for item in (details or ["нужно уточнение"])),
    ]
    if text:
        lines.extend(["", f"<b>Текст:</b> {esc(text[:500])}"])
    if similar:
        lines.extend(["", "<b>Похожие паттерны из базы:</b>"])
        lines.extend(f"• {esc(item)}" for item in similar[:3])
    lines.extend(["", "Выбери следующий статус кнопкой ниже."])
    return await send_with_keyboard(chat_id, "\n".join(lines), task_keyboard(task_id))


def parse_task_id_command(cmd: str, parts: list[str]) -> int | None:
    if len(parts) > 1 and parts[1].isdigit():
        return int(parts[1])
    match = re.fullmatch(r"/task(?:_)?(\d+)", cmd)
    return int(match.group(1)) if match else None


async def render_task(chat_id: int, db: sqlite3.Connection, task_id: int) -> dict[str, Any]:
    task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        return await send_rich(chat_id, f"❌ Задача #{task_id} не найдена")
    status = STATUS_LABELS.get(task["status"], esc(task["status"]))
    text = (
        f"<b>📋 Задача #{task['id']}</b>\n\n"
        f"<b>Название:</b> {esc(task['title'])}\n"
        f"<b>Статус:</b> {status}\n"
        f"<b>Описание:</b> {esc(str(task['description'] or '')[:700])}\n"
        f"<b>Кол-во:</b> {esc(task['quantity'] or '—')}\n"
        f"<b>Цена:</b> {esc(task['price'] or '—')}\n"
        f"<b>Поставщик:</b> {esc(task['supplier'] or '—')}\n"
        f"<b>Создана:</b> {esc(task['created_at'])}"
    )
    return await send_with_keyboard(chat_id, text, task_keyboard(task_id))


async def handle_command(chat_id: int, text: str, db: sqlite3.Connection) -> dict[str, Any]:
    parts = text.split()
    cmd = parts[0].lower()

    if cmd.startswith("/task"):
        task_id = parse_task_id_command(cmd, parts)
        if task_id is not None:
            return await render_task(chat_id, db, task_id)
        tasks = db.execute("SELECT id, title, status FROM tasks ORDER BY id DESC LIMIT 10").fetchall()
        if not tasks:
            return await send_rich(chat_id, "📭 В этой группе пока нет задач")
        lines = ["<b>📋 Последние задачи</b>", ""]
        for task in tasks:
            status = STATUS_LABELS.get(task["status"], "❓")
            lines.append(f"{status} — /task{task['id']} — {esc(task['title'])}")
        return await send_rich(chat_id, "\n".join(lines))

    if cmd == "/context":
        ctx = db.execute("SELECT key, value FROM context LIMIT 10").fetchall()
        if not ctx:
            return await send_rich(chat_id, "📭 Контекст группы пуст")
        lines = ["<b>📋 Контекст группы</b>", ""]
        lines.extend(f"• <b>{esc(row['key'])}:</b> {esc(str(row['value'])[:120])}" for row in ctx)
        return await send_rich(chat_id, "\n".join(lines))

    if cmd == "/status":
        count = db.execute("SELECT COUNT(*) AS c FROM tasks").fetchone()["c"]
        pending = db.execute("SELECT COUNT(*) AS c FROM tasks WHERE status='pending'").fetchone()["c"]
        done = db.execute("SELECT COUNT(*) AS c FROM tasks WHERE status='done'").fetchone()["c"]
        return await send_rich(
            chat_id,
            f"<b>📊 Статистика группы</b>\n\nЗадач всего: {count}\n⏳ Ожидают: {pending}\n✅ Выполнено: {done}",
        )

    if cmd == "/start":
        return await send_rich(
            chat_id,
            "<b>👋 Kairos Bot</b> — помощник по задачам в группе\n\n"
            "<b>Команды:</b>\n"
            "• /task — список задач\n"
            "• /task1 или /task 1 — детали задачи\n"
            "• /context — контекст группы\n"
            "• /status — статистика\n\n"
            "Отправь чертёж, деталь, материал, цену или потребность — я сохраню задачу отдельно для этой группы и покажу кнопки управления.",
        )

    return await send_rich(chat_id, f"❓ Неизвестная команда: {esc(cmd)}")


async def handle_callback_query(cb: dict[str, Any]) -> dict[str, Any] | None:
    chat_id = int(cb["message"]["chat"]["id"])
    data_text = cb.get("data", "")
    callback_id = cb.get("id", "")
    db = get_db(chat_id)

    try:
        if data_text.startswith("task_yes_"):
            orig_msg_id = int(data_text.replace("task_yes_", ""))
            msg_row = db.execute("SELECT text FROM messages WHERE msg_id=?", (orig_msg_id,)).fetchone()
            await api_call("answerCallbackQuery", callback_query_id=callback_id, text="Сохраняю задачу")
            if msg_row:
                pats = detect_patterns(msg_row["text"])
                return await handle_task(chat_id, msg_row["text"], pats, "confirmed", orig_msg_id, db, False)
            return await send_rich(chat_id, "⚠️ Не нашёл исходное сообщение")

        if data_text.startswith("task_no_"):
            await api_call("answerCallbackQuery", callback_query_id=callback_id, text="Отмечено: не задача")
            return await send_rich(chat_id, "Ок, это не задача. Оставляю как обычное сообщение.")

        if data_text.startswith("status_"):
            _, raw_task_id, status = data_text.split("_", 2)
            task_id = int(raw_task_id)
            if status not in STATUS_LABELS:
                await api_call("answerCallbackQuery", callback_query_id=callback_id, text="Неизвестный статус")
                return None
            db.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now_text(), task_id))
            db.commit()
            await api_call("answerCallbackQuery", callback_query_id=callback_id, text=STATUS_LABELS[status])
            return await send_rich(chat_id, f"Задача #{task_id}: {STATUS_LABELS[status]}")

        if data_text.startswith("show_"):
            task_id = int(data_text.replace("show_", ""))
            await api_call("answerCallbackQuery", callback_query_id=callback_id, text="Показываю детали")
            return await render_task(chat_id, db, task_id)

        await api_call("answerCallbackQuery", callback_query_id=callback_id, text="Готово")
    except Exception as exc:
        log.exception("Callback error")
        await api_call("answerCallbackQuery", callback_query_id=callback_id, text="Ошибка обработки")
        return await send_rich(chat_id, f"⚠️ Ошибка кнопки: {esc(exc)}")
    return None


async def handle_webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception as exc:
        return web.json_response({"ok": False, "error": str(exc)}, status=400)

    if "callback_query" in data:
        await handle_callback_query(data["callback_query"])
        return web.json_response({"ok": True})
    if "message" in data:
        msg = data["message"]
        chat_id = int(msg["chat"]["id"])
        db = get_db(chat_id)
        log.info("Message from chat %s: %s", chat_id, (msg.get("text") or msg.get("caption") or "")[:80])
        try:
            await process_message(chat_id, msg, db)
        except Exception as exc:
            log.exception("Message processing error")
            await send_rich(chat_id, f"⚠️ Ошибка обработки: {esc(exc)}")
    return web.json_response({"ok": True})


async def set_webhook() -> None:
    if not TOKEN:
        log.warning("KAIROS_TOKEN is empty; webhook is not configured")
        return
    async with aiohttp.ClientSession() as sess:
        async with sess.post(
            f"{API_BASE}/setWebhook",
            json={"url": WEBHOOK_URL, "allowed_updates": ["message", "callback_query"]},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            result = await resp.json(content_type=None)
            log.info("Webhook set: %s", result)


async def on_startup(app: web.Application) -> None:
    await set_webhook()


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook", handle_webhook)
    app.on_startup.append(on_startup)
    return app


app = create_app()

if __name__ == "__main__":
    log.info("Starting Kairos Bot on 0.0.0.0:%s", WEBHOOK_PORT)
    web.run_app(app, host="0.0.0.0", port=WEBHOOK_PORT)
