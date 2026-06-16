from __future__ import annotations

import csv
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import parser_result_rlm  # noqa: E402
import xlsx_exporter  # noqa: E402


def test_csv_to_xlsx_without_openpyxl_normalizes_and_dedupes(tmp_path: Path) -> None:
    source = tmp_path / "b2b.csv"
    with source.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["query", "title", "org", "link"])
        writer.writerow(["q", "line\nbreak", "A", "https://example.test/1"])
        writer.writerow(["q", "line break", "A", "https://example.test/1"])
        writer.writerow(["q", "other", "B", "https://example.test/2"])

    output = tmp_path / "b2b.xlsx"
    payload = xlsx_exporter.csv_to_xlsx(source, output, sheet_name="B2B", dedupe_columns=["link"])

    assert payload["rows"] == 2
    assert payload["columns"] == 4
    assert output.exists()
    with zipfile.ZipFile(output) as zf:
        sheet = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        assert "line break" in sheet
        assert "line\nbreak" not in sheet
        assert "https://example.test/2" in sheet

    summary = parser_result_rlm.summarize_parser_result(output)
    assert summary["format"] == "xlsx"
    assert summary["records"] == 2
    assert summary["columns"] == ["query", "title", "org", "link"]


def test_column_name_is_one_based() -> None:
    assert xlsx_exporter.column_name(1) == "A"
    assert xlsx_exporter.column_name(26) == "Z"
    assert xlsx_exporter.column_name(27) == "AA"
