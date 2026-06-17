"""Unit tests for the shared _common primitives."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from _common import gen_id, read_env_file, utc_now  # noqa: E402


def test_utc_now_is_second_precision_iso() -> None:
    value = utc_now()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00", value)


def test_gen_id_format_and_prefix() -> None:
    value = gen_id("proc")
    assert re.fullmatch(r"proc-\d{8}-\d{6}-[0-9a-f]{6}", value)


def test_gen_id_is_unique() -> None:
    assert gen_id("x") != gen_id("x")


def test_read_env_file_missing_returns_empty(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "nope.env") == {}


def test_read_env_file_parses_and_strips_quotes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# a comment",
                "",
                "PLAIN=value",
                'QUOTED="quoted value"',
                "SINGLE='single'",
                "  SPACED = spaced  ",
                "no_equals_line",
            ]
        )
    )
    data = read_env_file(env)
    assert data == {
        "PLAIN": "value",
        "QUOTED": "quoted value",
        "SINGLE": "single",
        "SPACED": "spaced",
    }
