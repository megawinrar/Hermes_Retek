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

    async def _run_hermes_process_callback(self, action: str, process_id: str, *, choice: str = "", reason: str = "") -> dict:
        """Run process_orchestrator.py for a Supervisor Telegram button."""
        import subprocess as _subprocess

        cmd = self._hermes_process_cli_base()
        if action == "decide":
            cmd += ["decide", process_id, "--choice", choice, "--reason", reason]
        elif action in {"show", "transcript"}:
            cmd += [action, process_id]
        else:
            return {"ok": False, "error": f"unsupported hermes_process action: {action}"}

        def _run() -> dict:
            completed = _subprocess.run(cmd, text=True, capture_output=True, check=False, timeout=45)
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
            return f"Hermes Supervisor\nAction: {action}\nError: {payload.get('error', 'unknown error')}"

        if action == "decide":
            next_action = payload.get("next_action") or {}
            lines = [
                "Hermes Supervisor decision recorded",
                f"Process: {payload.get('process_id', '')}",
                f"Status: {payload.get('status', '')}",
                f"Next action: {next_action.get('action', '')}",
            ]
            if next_action.get("required_fixes"):
                lines.append("Required fixes: " + "; ".join(str(item) for item in next_action.get("required_fixes", [])[:4]))
            if next_action.get("resume_hint"):
                lines.append("Resume: " + str(next_action.get("resume_hint"))[:900])
            return "\n".join(lines)

        summary = payload.get("summary") or {}
        if action == "show":
            bot2 = summary.get("bot2") or {}
            human = summary.get("human_decision") or {}
            lines = [
                "Hermes Supervisor process",
                f"Process: {summary.get('process_id') or payload.get('id', '')}",
                f"Status: {summary.get('status') or payload.get('status', '')}",
                f"Level: {summary.get('task_level', '')} / Risk: {summary.get('risk_level', '')}",
                f"Bot2: required={bot2.get('required')} status={bot2.get('status', '')}",
                f"Human: required={human.get('required')} status={human.get('status', '')}",
                f"Last event: {summary.get('last_event_type', '')}",
            ]
            next_action = summary.get("next_action") or {}
            if next_action:
                lines.append("Next action: " + str(next_action.get("action", "")))
            return "\n".join(lines)

        if action == "transcript":
            conversation = payload.get("conversation") or []
            lines = [
                "Hermes Supervisor transcript",
                f"Process: {payload.get('process_id', '')}",
                f"Status: {payload.get('status', '')}",
            ]
            for item in conversation[:6]:
                lines.append(f"- {item.get('actor', '')}: {item.get('phase', '')} / {item.get('status', '')}")
            review_cycles = payload.get("review_cycles") or []
            if review_cycles:
                lines.append(f"Review cycles: {len(review_cycles)}")
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
                await query.answer(text="Invalid Supervisor button data.")
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
                await query.answer(text="Not authorized to control Supervisor.")
                return

            user_display = getattr(query.from_user, "first_name", "User")
            action_map = {"y": "decide", "n": "decide", "s": "show", "t": "transcript"}
            action = action_map.get(action_token)
            if not action:
                await query.answer(text="Unknown Supervisor action.")
                return

            if action == "decide":
                choice = "yes" if action_token == "y" else "no"
                result = await self._run_hermes_process_callback(
                    "decide",
                    process_id,
                    choice=choice,
                    reason=f"Telegram button by {user_display}",
                )
                label = "YES: return Bot#1 to fixes" if choice == "yes" else "NO: accept Bot#1"
                await query.answer(text=label[:60])
                try:
                    await query.edit_message_text(
                        text=f"Hermes Supervisor: {label}\nProcess: {process_id}\nBy: {user_display}",
                        reply_markup=None,
                    )
                except Exception:
                    pass
                await self._send_hermes_process_callback_followup(
                    query,
                    self._format_hermes_process_callback_result("decide", result),
                )
                return

            result = await self._run_hermes_process_callback(action, process_id)
            await query.answer(text=("Process details" if action == "show" else "Transcript"))
            await self._send_hermes_process_callback_followup(
                query,
                self._format_hermes_process_callback_result(action, result),
            )
            return

'''


def patch_text(text: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    if HELPER_MARKER not in text:
        anchor = "    async def _handle_callback_query(\n"
        if anchor not in text:
            raise RuntimeError("callback handler anchor not found")
        text = text.replace(anchor, HELPER_BLOCK + anchor, 1)
        changes.append("helper_methods")
    if CALLBACK_MARKER not in text:
        anchor = "        # --- Update prompt callbacks ---\n"
        if anchor not in text:
            raise RuntimeError("update prompt anchor not found")
        text = text.replace(anchor, CALLBACK_BLOCK + anchor, 1)
        changes.append("callback_branch")
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
