from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dual_bot_lab  # noqa: E402


def test_bot1_prompt_preserves_retek_domain_name() -> None:
    messages = dual_bot_lab.bot1_messages(
        "Для CRM Ретек составь матрицу оценки поставщиков",
        "Не искажать название CRM Ретек.",
    )
    combined = "\n".join(message["content"] for message in messages)

    assert 'written in Russian as "Ретек"' in combined
    assert "CRM Ретек" in combined
    assert "Do not substitute" in combined


def test_bot2_prompt_does_not_require_future_supervisor_transcript() -> None:
    messages = dual_bot_lab.bot2_messages(
        "Live LLM smoke for CRM Ретек supplier analysis",
        "Supervisor transcript should show both bots after the run.",
        "Bot#1 answer with matrix and risks.",
    )
    combined = "\n".join(message["content"] for message in messages)

    assert "Supervisor transcript is generated after Bot#2 returns its verdict" in combined
    assert "must not mark a result as insufficient solely" in combined
    assert "future transcript is not embedded inside Bot#1" in combined
