#!/usr/bin/env python3
"""Install Telegram callback buttons for Hermes Retek Supervisor.

This patches the live upstream Hermes Telegram adapter in-place. The patch is
small and idempotent: it adds an ``hp:`` callback prefix that calls the
host-side ``process_orchestrator.py`` for Supervisor human-gate actions.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


DEFAULT_TARGET = Path("/opt/hermes-assistant/hermes-core/gateway/platforms/telegram.py")
HELPER_MARKER = "_run_hermes_process_callback"
CALLBACK_MARKER = "# --- Hermes process supervisor callbacks (hp:action:process_id) ---"
HELPER_CONTINUE_MARKER = 'elif action == "continue":'
HELPER_FAST_PATH_MARKER = "_run_hermes_process_tool_callback"
CALLBACK_CONTINUE_MARKER = 'result["continue_result"] = await self._run_hermes_process_callback("continue", process_id)'
HELPER_RU_MARKER = "Автопродолжение после Да"
CALLBACK_RU_MARKER = "Да: вернуть Bot#1 на доработку"


HELPER_BLOCK = r'''
    def _hermes_process_cli_base(self) -> list[str]:
        """Return the Retek process orchestrator command base for Telegram buttons."""
        import sys as _sys
        return [
            _sys.executable,
            os.environ.get("HERMES_PROCESS_ORCHESTRATOR", "/opt/hermes-assistant/scripts/process_orchestrator.py"),
            "--process-store",
            os.environ.get("HERMES_PROCESS_STORE", "/opt/data/process_orchestrator_store.db"),
            "--supervisor-store",
            os.environ.get("HERMES_SUPERVISOR_STORE", "/opt/data/supervisor_store.db"),
        ]

    def _hermes_process_callback_payload(self, action: str, process_id: str, *, choice: str = "", reason: str = "") -> dict:
        """Build a hermes_process tool payload for Supervisor Telegram buttons."""
        payload = {
            "action": action,
            "process_id": process_id,
            "include_raw": True,
            "execution_mode": os.environ.get("HERMES_PROCESS_EXECUTION_MODE", "in_process"),
        }
        if action == "decide":
            payload.update({"choice": choice, "reason": reason})
        elif action == "continue":
            payload.update({"mode": "auto", "notify_telegram": True})
        elif action not in {"show", "transcript"}:
            return {"ok": False, "error": f"unsupported hermes_process action: {action}"}
        return payload

    def _run_hermes_process_tool_callback(self, action: str, process_id: str, *, choice: str = "", reason: str = "") -> dict:
        """Run the already-mounted hermes_process tool without spawning Python."""
        import importlib as _importlib

        hermes_process_tool = _importlib.import_module("tools.hermes_process_tool")
        payload = self._hermes_process_callback_payload(action, process_id, choice=choice, reason=reason)
        if not payload.get("ok", True):
            return payload
        raw = hermes_process_tool.execute(**payload)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return {"ok": False, "error": "unexpected hermes_process tool payload"}
        if not parsed.get("ok", True):
            return parsed
        result = parsed.get("raw") if isinstance(parsed.get("raw"), dict) else parsed
        result.setdefault("ok", True)
        if parsed.get("adapter"):
            result["adapter"] = parsed.get("adapter")
        return result

    async def _run_hermes_process_callback(self, action: str, process_id: str, *, choice: str = "", reason: str = "") -> dict:
        """Run hermes_process for a Supervisor Telegram button."""
        import subprocess as _subprocess

        cmd = self._hermes_process_cli_base()
        if action == "decide":
            cmd += ["decide", process_id, "--choice", choice, "--reason", reason]
        elif action == "continue":
            cmd += ["continue", process_id, "--mode", "auto", "--notify-telegram"]
        elif action in {"show", "transcript"}:
            cmd += [action, process_id]
        else:
            return {"ok": False, "error": f"unsupported hermes_process action: {action}"}

        def _timeout_seconds() -> int:
            raw = os.environ.get("HERMES_PROCESS_CALLBACK_TIMEOUT", "300" if action == "continue" else "45")
            try:
                return max(30, min(900, int(raw)))
            except Exception:
                return 300 if action == "continue" else 45

        def _run() -> dict:
            try:
                return self._run_hermes_process_tool_callback(action, process_id, choice=choice, reason=reason)
            except Exception:
                pass
            completed = _subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=_timeout_seconds())
            if completed.returncode != 0:
                return {
                    "ok": False,
                    "error": (completed.stderr.strip() or completed.stdout.strip() or f"exit {completed.returncode}")[:1800],
                }
            try:
                payload = json.loads(completed.stdout)
            except Exception as exc:
                return {"ok": False, "error": f"invalid JSON from process_orchestrator: {type(exc).__name__}"}
            if isinstance(payload, dict):
                payload.setdefault("ok", True)
                return payload
            return {"ok": False, "error": "unexpected process_orchestrator payload"}

        return await asyncio.to_thread(_run)

    def _format_hermes_process_callback_result(self, action: str, payload: dict) -> str:
        """Compact text for Supervisor button follow-up messages."""
        if not payload.get("ok", True):
            return f"Hermes Supervisor\nДействие: {action}\nОшибка: {payload.get('error', 'unknown error')}"

        if action == "decide":
            next_action = payload.get("next_action") or {}
            lines = [
                "Hermes Supervisor: решение записано",
                f"Процесс: {payload.get('process_id', '')}",
                f"Статус: {payload.get('status', '')}",
                f"Следующее действие: {next_action.get('action', '')}",
            ]
            continue_result = payload.get("continue_result") or {}
            if continue_result:
                continue_next = continue_result.get("next_action") or {}
                continue_bot2 = continue_result.get("bot2_verdict") or {}
                lines.extend(
                    [
                        "",
                        "Автопродолжение после Да",
                        f"Режим: {continue_result.get('mode', '')}",
                        f"Статус: {continue_result.get('status', '')}",
                        f"Bot2: {continue_bot2.get('status', '')}",
                        f"Следующее действие: {continue_next.get('action', '')}",
                    ]
                )
                if continue_result.get("report_path"):
                    lines.append("Отчет: " + str(continue_result.get("report_path"))[:900])
                if continue_result.get("notification_delivery"):
                    delivery = continue_result.get("notification_delivery") or {}
                    lines.append(
                        "Повторный human-gate: "
                        + ("отправлен" if delivery.get("telegram_delivered") else str(delivery.get("mode", "recorded")))
                    )
            if next_action.get("required_fixes"):
                lines.append("Что исправить: " + "; ".join(str(item) for item in next_action.get("required_fixes", [])[:4]))
            if next_action.get("resume_hint") and not continue_result:
                lines.append("Как продолжить: " + str(next_action.get("resume_hint"))[:900])
            return "\n".join(lines)

        summary = payload.get("summary") or {}
        if action == "show":
            bot2 = summary.get("bot2") or {}
            human = summary.get("human_decision") or {}
            lines = [
                "Hermes Supervisor: процесс",
                f"Процесс: {summary.get('process_id') or payload.get('id', '')}",
                f"Статус: {summary.get('status') or payload.get('status', '')}",
                f"Уровень: {summary.get('task_level', '')} / риск: {summary.get('risk_level', '')}",
                f"Bot2: required={bot2.get('required')} status={bot2.get('status', '')}",
                f"Human: required={human.get('required')} status={human.get('status', '')}",
                f"Последнее событие: {summary.get('last_event_type', '')}",
            ]
            next_action = summary.get("next_action") or {}
            if next_action:
                lines.append("Следующее действие: " + str(next_action.get("action", "")))
            return "\n".join(lines)

        if action == "transcript":
            conversation = payload.get("conversation") or []
            lines = [
                "Hermes Supervisor: лог диалога",
                f"Процесс: {payload.get('process_id', '')}",
                f"Статус: {payload.get('status', '')}",
            ]
            for item in conversation[:6]:
                lines.append(f"- {item.get('actor', '')}: {item.get('phase', '')} / {item.get('status', '')}")
            review_cycles = payload.get("review_cycles") or []
            if review_cycles:
                lines.append(f"Циклов проверки: {len(review_cycles)}")
            return "\n".join(lines)

        return json.dumps(payload, ensure_ascii=False)[:1800]

    async def _send_hermes_process_callback_followup(self, query, text: str) -> None:
        """Send Supervisor callback result in the same chat/topic as the button."""
        if not getattr(query, "message", None):
            return
        message = query.message
        chat_id = getattr(message, "chat_id", None)
        if chat_id is None:
            return
        thread_id = getattr(message, "message_thread_id", None)
        send_kwargs: Dict[str, Any] = {
            "chat_id": int(chat_id),
            "text": text[:3900],
            **self._link_preview_kwargs(),
        }
        if thread_id is not None:
            send_kwargs.update(
                self._thread_kwargs_for_send(
                    str(chat_id),
                    str(thread_id),
                    {"thread_id": str(thread_id)},
                    reply_to_mode=self._reply_to_mode,
                )
            )
        await self._send_message_with_thread_fallback(**send_kwargs)

'''


CALLBACK_BLOCK = r'''
        # --- Hermes process supervisor callbacks (hp:action:process_id) ---
        if data.startswith("hp:"):
            parts = data.split(":", 2)
            if len(parts) != 3:
                await query.answer(text="Некорректная кнопка Supervisor.")
                return
            action_token = parts[1]
            process_id = parts[2].strip()
            caller_id = str(getattr(query.from_user, "id", ""))
            if not self._is_callback_user_authorized(
                caller_id,
                chat_id=query_chat_id,
                chat_type=str(query_chat_type) if query_chat_type is not None else None,
                thread_id=str(query_thread_id) if query_thread_id is not None else None,
                user_name=query_user_name,
            ):
                await query.answer(text="Нет прав управлять Supervisor.")
                return

            user_display = getattr(query.from_user, "first_name", "User")
            action_map = {"y": "decide", "n": "decide", "s": "show", "t": "transcript"}
            action = action_map.get(action_token)
            if not action:
                await query.answer(text="Неизвестное действие Supervisor.")
                return

            if action == "decide":
                choice = "yes" if action_token == "y" else "no"
                result = await self._run_hermes_process_callback(
                    "decide",
                    process_id,
                    choice=choice,
                    reason=f"Telegram button by {user_display}",
                )
                label = "Да: вернуть Bot#1 на доработку" if choice == "yes" else "Нет: принять Bot#1"
                await query.answer(text=label[:60])
                try:
                    await query.edit_message_text(
                        text=f"Hermes Supervisor: {label}\nПроцесс: {process_id}\nКто нажал: {user_display}",
                        reply_markup=None,
                    )
                except Exception:
                    pass
                if choice == "yes" and result.get("ok", True):
                    result["continue_result"] = await self._run_hermes_process_callback("continue", process_id)
                await self._send_hermes_process_callback_followup(
                    query,
                    self._format_hermes_process_callback_result("decide", result),
                )
                return

            result = await self._run_hermes_process_callback(action, process_id)
            await query.answer(text=("Детали процесса" if action == "show" else "Лог диалога"))
            await self._send_hermes_process_callback_followup(
                query,
                self._format_hermes_process_callback_result(action, result),
            )
            return

'''


def patch_text(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    callback_anchor = "    async def _handle_callback_query(\n"
    update_anchor = "        # --- Update prompt callbacks ---\n"
    if HELPER_MARKER not in text:
        if callback_anchor not in text:
            raise RuntimeError("callback handler anchor not found")
        text = text.replace(callback_anchor, HELPER_BLOCK + callback_anchor, 1)
        changes.append("helper_methods")
    elif HELPER_CONTINUE_MARKER not in text or HELPER_FAST_PATH_MARKER not in text or HELPER_RU_MARKER not in text:
        helper_start = text.find("    def _hermes_process_cli_base(self) -> list[str]:\n")
        helper_end = text.find(callback_anchor)
        if helper_start < 0 or helper_end < 0 or helper_start >= helper_end:
            raise RuntimeError("existing helper block anchors not found")
        text = text[:helper_start] + HELPER_BLOCK + text[helper_end:]
        changes.append("helper_methods_upgrade")
    if CALLBACK_MARKER not in text:
        if update_anchor not in text:
            raise RuntimeError("update prompt anchor not found")
        text = text.replace(update_anchor, CALLBACK_BLOCK + update_anchor, 1)
        changes.append("callback_branch")
    elif CALLBACK_CONTINUE_MARKER not in text or CALLBACK_RU_MARKER not in text:
        marker_start = text.find(CALLBACK_MARKER)
        callback_start = text.rfind("\n", 0, marker_start) + 1
        callback_end = text.find(update_anchor)
        if marker_start < 0 or callback_start < 0 or callback_end < 0 or callback_start >= callback_end:
            raise RuntimeError("existing callback block anchors not found")
        text = text[:callback_start] + CALLBACK_BLOCK + text[callback_end:]
        changes.append("callback_branch_upgrade")
    return text, changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--backup", type=Path, default=None)
    args = parser.parse_args()

    original = args.target.read_text()
    patched, changes = patch_text(original)
    if not changes:
        print("already_patched")
        return
    backup = args.backup or args.target.with_suffix(args.target.suffix + ".before_supervisor_buttons.bak")
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.target, backup)
    args.target.write_text(patched)
    print("patched " + ",".join(changes))
    print(f"backup {backup}")


if __name__ == "__main__":
    main()
