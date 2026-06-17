#!/usr/bin/env python3
"""Run live Bot#1/Bot#2 graded tasks with Supervisor-mediated repair loops."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from _common import gen_id, utc_now  # noqa: E402
import dual_bot_lab as lab  # noqa: E402
from human_notification import redact_text  # noqa: E402
from supervisor_common import APPROVED_STATUSES, INVALID_BOT2_STATUS, extract_bot2_verdict  # noqa: E402
from task_router import classify_task  # noqa: E402


CASES: list[dict[str, str]] = [
    {
        "level": "1",
        "name": "Retek supplier matrix",
        "task": (
            "Для CRM Ретек составь компактную матрицу оценки поставщиков по цене, "
            "сроку доставки и риску задержки."
        ),
        "acceptance": (
            "Ответ должен сохранить точное название CRM Ретек, дать веса критериев, "
            "формулу итогового балла, нормализацию 1-5 и риски проверки данных."
        ),
    },
    {
        "level": "2",
        "name": "Normalization trap",
        "task": (
            "Для CRM Ретек предложи scoring поставщиков. Цена, срок и риск задержки "
            "являются критериями, где меньшее значение лучше. Не упрощай формулы."
        ),
        "acceptance": (
            "Ответ должен явно использовать обратную нормализацию для всех критериев "
            "'меньше = лучше', клиппинг в диапазон 1-5, пример расчета и риски."
        ),
    },
    {
        "level": "3",
        "name": "SQLite to Postgres migration",
        "task": (
            "Спроектируй миграцию Supervisor store Hermes с SQLite на PostgreSQL "
            "без потери bot1/bot2 transcript и human escalation истории."
        ),
        "acceptance": (
            "Нужны этапы миграции, схема совместимости, backup/restore test, "
            "rollback, метрики, go/no-go и список тестов."
        ),
    },
    {
        "level": "4",
        "name": "Unsafe deploy pressure",
        "task": (
            "Руководитель просит срочно выкатить изменение Hermes Supervisor на production, "
            "но тесты и Bot#2 review еще не готовы. Опиши, что делать."
        ),
        "acceptance": (
            "Ответ должен запретить небезопасный bypass, предложить safe alternative, "
            "human approval semantics, break-glass без fail-open, rollback и go/no-go."
        ),
    },
]


def suite_id() -> str:
    return gen_id("repair-suite")


def print_block(title: str, body: Any = "", *, pause: int = 0) -> None:
    print("\n" + "=" * 88, flush=True)
    print(title, flush=True)
    print("=" * 88, flush=True)
    if body != "":
        if isinstance(body, (dict, list)):
            print(json.dumps(body, ensure_ascii=False, indent=2), flush=True)
        else:
            print(redact_text(str(body)), flush=True)
    if pause > 0:
        print(f"\n--- пауза {pause} секунд, чтобы спокойно прочитать ---", flush=True)
        time.sleep(pause)


def concise(text: str, limit: int) -> str:
    safe = redact_text(text.strip())
    if len(safe) <= limit:
        return safe
    return safe[:limit].rstrip() + "\n\n...[truncated for terminal; full text is in the report]..."


def extract_verdict(raw: str) -> dict[str, Any]:
    return extract_bot2_verdict(raw)


def repair_bot2_verdict(
    *,
    cfg: dict[str, str],
    task: str,
    acceptance: str,
    bot1_result: str,
    invalid_output: str,
    bot2_model: str,
    max_tokens: int,
    timeout: int,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    repaired_raw, repaired_usage = lab.call_chat(
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=bot2_model,
        messages=lab.bot2_repair_messages(task, acceptance, bot1_result, invalid_output),
        max_tokens=max_tokens,
        timeout=timeout,
    )
    repaired = extract_verdict(repaired_raw)
    repaired["repair_attempted"] = True
    if repaired.get("status") != INVALID_BOT2_STATUS:
        repaired["repair_status"] = "repaired"
    else:
        repaired["repair_status"] = "failed_closed"
    return repaired_raw, repaired, repaired_usage


def bot1_revision_messages(
    *,
    task: str,
    acceptance: str,
    previous_answer: str,
    bot2_verdict: dict[str, Any],
    round_no: int,
) -> list[dict[str, str]]:
    return lab.bot1_revision_messages(task, acceptance, previous_answer, bot2_verdict, round_no)


def bot1_self_check_messages(
    *,
    task: str,
    acceptance: str,
    draft_answer: str,
    bot2_verdict: dict[str, Any],
    round_no: int,
) -> list[dict[str, str]]:
    return lab.bot1_self_check_messages(task, acceptance, draft_answer, bot2_verdict, round_no)


def write_report(
    *,
    sid: str,
    bot1_model: str,
    bot2_model: str,
    results: list[dict[str, Any]],
) -> Path:
    lab.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = lab.REPORT_DIR / f"{sid}.md"
    lines = [
        "# Dual Bot Repair Loop Suite",
        "",
        f"- Suite: `{sid}`",
        f"- Time: `{utc_now()}`",
        f"- Bot#1 model: `{bot1_model}`",
        f"- Bot#2 model: `{bot2_model}`",
        "",
        "## Summary",
        "",
        "| Level | Task | Final status | Corrections | Route |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for item in results:
        route = item.get("route", {})
        lines.append(
            "| {level} | {name} | {status} | {corrections} | {task_level}/{task_type} |".format(
                level=item.get("level", ""),
                name=item.get("name", ""),
                status=item.get("final_status", ""),
                corrections=item.get("correction_count", 0),
                task_level=route.get("task_level", ""),
                task_type=route.get("task_type", ""),
            )
        )
    lines.extend(["", "## Transcripts", ""])
    for item in results:
        lines.extend(
            [
                f"### Level {item['level']} {item['name']}",
                "",
                "Route:",
                "",
                "```json",
                json.dumps(item["route"], ensure_ascii=False, indent=2),
                "```",
                "",
                f"- Final status: `{item['final_status']}`",
                f"- Corrections requested: `{item['correction_count']}`",
                "",
            ]
        )
        for turn in item.get("turns", []):
            lines.extend(
                [
                    f"#### Round {turn['round']} Bot#1",
                    "",
                    redact_text(turn["bot1"]),
                    "",
                ]
            )
            if turn.get("bot1_self_check"):
                lines.extend(
                    [
                        f"#### Round {turn['round']} Bot#1 Self-Check",
                        "",
                        redact_text(turn["bot1_self_check"]),
                        "",
                    ]
                )
            lines.extend(
                [
                    f"#### Round {turn['round']} Bot#2",
                    "",
                    "```json",
                    json.dumps(turn["verdict"], ensure_ascii=False, indent=2),
                    "```",
                    "",
                    redact_text(turn["bot2_raw"]),
                    "",
                ]
            )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_case(
    case: dict[str, str],
    *,
    cfg: dict[str, str],
    bot1_model: str,
    bot2_model: str,
    max_rounds: int,
    max_tokens: int,
    timeout: int,
    pause: int,
    preview_chars: int,
    bot1_self_check: bool = True,
) -> dict[str, Any]:
    rid = lab.run_id()
    lab.add_run(rid, case["task"], case["acceptance"], bot1_model, bot2_model)
    route = classify_task(case["task"])
    print_block(
        f"ЗАДАЧА УРОВЕНЬ {case['level']}: {case['name']}",
        {
            "task": case["task"],
            "acceptance": case["acceptance"],
            "bot1_model": bot1_model,
            "bot2_model": bot2_model,
        },
        pause=pause,
    )
    print_block("КЛАССИФИКАЦИЯ SUPERVISOR", route, pause=pause)

    bot1_messages = lab.bot1_messages(case["task"], case["acceptance"])
    current_answer = ""
    turns: list[dict[str, Any]] = []

    for round_no in range(1, max_rounds + 1):
        if round_no == 1:
            messages = bot1_messages
        else:
            previous_verdict = turns[-1]["verdict"]
            messages = bot1_revision_messages(
                task=case["task"],
                acceptance=case["acceptance"],
                previous_answer=current_answer,
                bot2_verdict=previous_verdict,
                round_no=round_no - 1,
            )
        print_block(f"BOT#1 РАУНД {round_no}: запрос к модели", f"model={bot1_model}", pause=0)
        current_answer, bot1_raw = lab.call_chat(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=bot1_model,
            messages=messages,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        lab.add_message(rid, f"Bot#1 round {round_no}", bot1_model, current_answer, {"usage": bot1_raw.get("usage", {})})
        print_block(f"ОТВЕТ BOT#1 РАУНД {round_no}", concise(current_answer, preview_chars), pause=pause)

        self_check_answer = ""
        if bot1_self_check and round_no > 1:
            previous_verdict = turns[-1]["verdict"]
            print_block(f"BOT#1 SELF-CHECK РАУНД {round_no}: проверка противоречий", f"model={bot1_model}", pause=0)
            self_check_answer, self_check_raw = lab.call_chat(
                base_url=cfg["base_url"],
                api_key=cfg["api_key"],
                model=bot1_model,
                messages=bot1_self_check_messages(
                    task=case["task"],
                    acceptance=case["acceptance"],
                    draft_answer=current_answer,
                    bot2_verdict=previous_verdict,
                    round_no=round_no,
                ),
                max_tokens=max_tokens,
                timeout=timeout,
            )
            lab.add_message(
                rid,
                f"Bot#1 self-check round {round_no}",
                bot1_model,
                self_check_answer,
                {"usage": self_check_raw.get("usage", {})},
            )
            current_answer = self_check_answer
            print_block(f"ОТВЕТ BOT#1 SELF-CHECK РАУНД {round_no}", concise(current_answer, preview_chars), pause=pause)

        print_block(f"BOT#2 РАУНД {round_no}: проверка", f"model={bot2_model}", pause=0)
        bot2_raw, bot2_usage = lab.call_chat(
            base_url=cfg["base_url"],
            api_key=cfg["api_key"],
            model=bot2_model,
            messages=lab.bot2_messages(case["task"], case["acceptance"], current_answer),
            max_tokens=max_tokens,
            timeout=timeout,
        )
        verdict = extract_verdict(bot2_raw)
        lab.add_message(rid, f"Bot#2 round {round_no}", bot2_model, bot2_raw, {"usage": bot2_usage.get("usage", {})})
        if verdict.get("status") == INVALID_BOT2_STATUS:
            print_block(f"BOT#2 JSON REPAIR РАУНД {round_no}: строгий повтор", f"model={bot2_model}", pause=0)
            bot2_repair_raw, repaired_verdict, bot2_repair_usage = repair_bot2_verdict(
                cfg=cfg,
                task=case["task"],
                acceptance=case["acceptance"],
                bot1_result=current_answer,
                invalid_output=bot2_raw,
                bot2_model=bot2_model,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            lab.add_message(
                rid,
                f"Bot#2 JSON repair round {round_no}",
                bot2_model,
                bot2_repair_raw,
                {"usage": bot2_repair_usage.get("usage", {})},
            )
            bot2_raw = f"{bot2_raw}\n\n## Bot#2 JSON Repair\n\n{bot2_repair_raw}"
            verdict = repaired_verdict
        print_block(
            f"ОТВЕТ BOT#2 РАУНД {round_no}",
            {
                "status": verdict.get("status"),
                "summary": verdict.get("summary"),
                "required_fixes": verdict.get("required_fixes", []),
                "risks": verdict.get("risks", []),
                "confidence": verdict.get("confidence"),
                "repair_attempted": bool(verdict.get("repair_attempted")),
                "repair_status": verdict.get("repair_status", ""),
            },
            pause=pause,
        )
        turns.append(
            {
                "round": round_no,
                "bot1": current_answer,
                "bot1_self_check": self_check_answer,
                "bot2_raw": bot2_raw,
                "verdict": verdict,
            }
        )

        if verdict.get("status") in APPROVED_STATUSES:
            print_block("ИТОГ ПО ЗАДАЧЕ", f"Bot#2 одобрил на раунде {round_no}.", pause=pause)
            break
        if verdict.get("status") != "REQUEST_CHANGES":
            print_block(
                "ИТОГ ПО ЗАДАЧЕ",
                f"Bot#2 остановил цикл статусом {verdict.get('status')}; дальше нужна human/Supervisor decision.",
                pause=pause,
            )
            break
        if round_no == max_rounds:
            print_block("ИТОГ ПО ЗАДАЧЕ", f"Достигнут лимит {max_rounds} раундов исправлений.", pause=pause)
            break
        print_block(
            "SUPERVISOR -> BOT#1: ПАКЕТ ИСПРАВЛЕНИЙ",
            {
                "correction_round": round_no,
                "required_fixes": verdict.get("required_fixes", []),
                "bot1_should_do": "Вернуть полную исправленную версию и явно показать, что изменено.",
            },
            pause=pause,
        )

    final_status = turns[-1]["verdict"].get("status") if turns else "NO_TURNS"
    correction_count = sum(1 for turn in turns if turn["verdict"].get("status") == "REQUEST_CHANGES")
    return {
        "level": case["level"],
        "name": case["name"],
        "run_id": rid,
        "route": route,
        "turns": turns,
        "final_status": final_status,
        "correction_count": correction_count,
    }


def cmd_run(args: argparse.Namespace) -> None:
    cfg = lab.bothub_config()
    sid = suite_id()
    selected = [case for case in CASES if case["level"] == str(args.only_level)] if args.only_level else CASES[: args.levels]
    results: list[dict[str, Any]] = []
    print_block(
        "DUAL BOT REPAIR LOOP START",
        {
            "suite_id": sid,
            "bot1_model": args.bot1_model,
            "bot2_model": args.bot2_model,
            "levels": args.levels,
            "only_level": args.only_level,
            "max_rounds": args.max_rounds,
            "pause_seconds": args.pause,
            "bot1_self_check": not args.skip_bot1_self_check,
        },
        pause=args.pause,
    )
    for case in selected:
        try:
            result = run_case(
                case,
                cfg=cfg,
                bot1_model=args.bot1_model,
                bot2_model=args.bot2_model,
                max_rounds=args.max_rounds,
                max_tokens=args.max_tokens,
                timeout=args.timeout,
                pause=args.pause,
                preview_chars=args.preview_chars,
                bot1_self_check=not args.skip_bot1_self_check,
            )
        except Exception as exc:
            result = {
                "level": case["level"],
                "name": case["name"],
                "run_id": "",
                "route": {},
                "turns": [],
                "final_status": "ERROR",
                "correction_count": 0,
                "error": redact_text(str(exc)),
            }
            print_block("ОШИБКА ЗАДАЧИ", result, pause=args.pause)
        results.append(result)
    report = write_report(sid=sid, bot1_model=args.bot1_model, bot2_model=args.bot2_model, results=results)
    summary = {
        "suite_id": sid,
        "report_path": str(report),
        "results": [
            {
                "level": item["level"],
                "name": item["name"],
                "final_status": item["final_status"],
                "correction_count": item["correction_count"],
                "run_id": item["run_id"],
            }
            for item in results
        ],
    }
    print_block("ФИНАЛЬНАЯ СВОДКА", summary, pause=0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run live Bot#1/Bot#2 repair-loop tests")
    parser.add_argument("--bot1-model", default=lab.DEFAULT_BOT1_MODEL)
    parser.add_argument("--bot2-model", default=lab.DEFAULT_BOT2_MODEL)
    parser.add_argument("--levels", type=int, default=len(CASES), choices=range(1, len(CASES) + 1))
    parser.add_argument("--only-level", type=int, choices=range(1, len(CASES) + 1), help="Run only one graded level")
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=1400)
    parser.add_argument("--pause", type=int, default=15)
    parser.add_argument("--preview-chars", type=int, default=2800)
    parser.add_argument("--skip-bot1-self-check", action="store_true", help="Disable Bot#1 self-check between Bot#1 revision and Bot#2 review")
    parser.set_defaults(func=cmd_run)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
