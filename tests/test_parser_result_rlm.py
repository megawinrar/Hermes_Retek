from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import parser_result_rlm  # noqa: E402
import rlm_store  # noqa: E402


def test_infer_parser_result_paths_keeps_explicit_artifacts() -> None:
    paths = parser_result_rlm.infer_parser_result_paths(
        {"cmd": "node /opt/data/rebrowser/parser.js"},
        "saved /opt/data/rebrowser/b2b-results-v4.json, /opt/data/rebrowser/export.csv)",
    )

    assert paths == [
        "/opt/data/rebrowser/b2b-results-v4.json",
        "/opt/data/rebrowser/export.csv",
    ]


def test_infer_parser_result_paths_from_rebrowser_search_script() -> None:
    paths = parser_result_rlm.infer_parser_result_paths(
        {"code": "node /opt/data/rebrowser/b2b-search-v4.js"},
        "Done: counted 13 keyword buckets",
    )

    assert "/opt/data/rebrowser/b2b-results-v4.json" in paths


def test_infer_parser_result_paths_deduplicates_inferred_and_explicit_path() -> None:
    paths = parser_result_rlm.infer_parser_result_paths(
        {"cmd": "node /opt/data/rebrowser/b2b-search.js"},
        "output=/opt/data/rebrowser/b2b-results.json",
    )

    assert paths == ["/opt/data/rebrowser/b2b-results.json"]


def test_summarize_b2b_sales_json_counts_nested_sales(tmp_path: Path) -> None:
    result = tmp_path / "b2b-results-v4.json"
    result.write_text(
        json.dumps(
            [
                {"query": "q1", "sales": [{"title": "a"}, {"title": "b"}]},
                {"query": "q2", "sales": [{"title": "c"}]},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = parser_result_rlm.summarize_parser_result(result)

    assert summary["format"] == "json"
    assert summary["records"] == 3
    assert summary["queries"] == ["q1", "q2"]
    assert summary["size_bytes"] > 0


def test_summarize_csv_counts_data_rows(tmp_path: Path) -> None:
    result = tmp_path / "kontur.csv"
    result.write_text("title,org\none,a\ntwo,b\n", encoding="utf-8")

    summary = parser_result_rlm.summarize_parser_result(result)

    assert summary["format"] == "csv"
    assert summary["records"] == 2
    assert summary["columns"] == ["title", "org"]


def test_write_parser_result_lesson_to_rlm(tmp_path: Path) -> None:
    store = tmp_path / "rlm.db"
    result = tmp_path / "b2b-results-v4.json"
    result.write_text(json.dumps([{"query": "q", "sales": [{"title": "a"}]}]), encoding="utf-8")

    record = parser_result_rlm.write_parser_result_lesson(
        result,
        store_path=store,
        process_id="proc-b2b",
        site="b2b_center",
        script_path="/opt/data/rebrowser/b2b-search-v4.js",
    )
    duplicate = parser_result_rlm.write_parser_result_lesson(
        result,
        store_path=store,
        process_id="proc-b2b",
        site="b2b_center",
    )
    found = rlm_store.search_records(kind="parser_result", process_id="proc-b2b", store_path=store)

    assert record["kind"] == "parser_result"
    assert duplicate["id"] == record["id"]
    assert duplicate["duplicate"] is True
    assert len(found) == 1
    assert found[0]["metadata"]["records"] == 1
    assert "site/b2b_center" in found[0]["tags"]
    assert "b2b-results-v4.json" in found[0]["summary"]


def test_cli_summary_only(tmp_path: Path) -> None:
    result = tmp_path / "result.csv"
    result.write_text("a\n1\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/parser_result_rlm.py"),
            str(result),
            "--summary-only",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["records"] == 1
    assert payload["format"] == "csv"
