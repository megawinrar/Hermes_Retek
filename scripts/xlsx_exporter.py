#!/usr/bin/env python3
"""Dependency-free CSV to XLSX exporter for Hermes parser artifacts."""

from __future__ import annotations

import argparse
import csv
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape


CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clean_cell(value: object, *, single_line: bool = True) -> str:
    text = CONTROL_RE.sub("", str(value if value is not None else ""))
    if single_line:
        text = " ".join(text.split())
    return text[:32767]


def column_name(index: int) -> str:
    if index < 1:
        raise ValueError("column index is 1-based")
    result = ""
    while index:
        index, rem = divmod(index - 1, 26)
        result = chr(65 + rem) + result
    return result


def read_csv(path: Path, *, single_line: bool = True) -> list[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [[clean_cell(cell, single_line=single_line) for cell in row] for row in csv.reader(fh)]


def dedupe_rows(rows: list[list[str]], *, key_columns: Iterable[str] = ()) -> list[list[str]]:
    keys = [key.strip() for key in key_columns if key.strip()]
    if not rows or not keys:
        return rows
    header = rows[0]
    indexes = [header.index(key) for key in keys if key in header]
    if not indexes:
        return rows
    result = [header]
    seen: set[tuple[str, ...]] = set()
    for row in rows[1:]:
        key = tuple(row[idx] if idx < len(row) else "" for idx in indexes)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def sheet_xml(rows: list[list[str]]) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for row_idx, row in enumerate(rows, start=1):
        parts.append(f'<row r="{row_idx}">')
        for col_idx, value in enumerate(row, start=1):
            ref = f"{column_name(col_idx)}{row_idx}"
            parts.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        parts.append("</row>")
    parts.extend(["</sheetData>", "</worksheet>"])
    return "".join(parts)


def write_xlsx(rows: list[list[str]], output: Path, *, sheet_name: str = "Results") -> dict[str, object]:
    output.parent.mkdir(parents=True, exist_ok=True)
    safe_sheet = clean_cell(sheet_name, single_line=True)[:31] or "Results"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
</Types>""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""",
        "docProps/app.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
<Application>Hermes Retek</Application></Properties>""",
        "docProps/core.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:creator>Hermes Retek</dc:creator><cp:lastModifiedBy>Hermes Retek</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified>
</cp:coreProperties>""",
        "xl/workbook.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="{escape(safe_sheet)}" sheetId="1" r:id="rId1"/></sheets></workbook>""",
        "xl/_rels/workbook.xml.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>""",
        "xl/styles.xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts><fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs></styleSheet>""",
        "xl/worksheets/sheet1.xml": sheet_xml(rows),
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return {
        "path": str(output),
        "rows": max(0, len(rows) - 1 if rows else 0),
        "columns": len(rows[0]) if rows else 0,
        "size_bytes": output.stat().st_size,
        "sheet": safe_sheet,
    }


def csv_to_xlsx(
    csv_path: Path | str,
    xlsx_path: Path | str | None = None,
    *,
    sheet_name: str = "Results",
    dedupe_columns: Iterable[str] = (),
    single_line: bool = True,
) -> dict[str, object]:
    source = Path(csv_path)
    output = Path(xlsx_path) if xlsx_path else source.with_suffix(".xlsx")
    rows = dedupe_rows(read_csv(source, single_line=single_line), key_columns=dedupe_columns)
    return write_xlsx(rows, output, sheet_name=sheet_name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path")
    parser.add_argument("xlsx_path", nargs="?")
    parser.add_argument("--sheet-name", default="Results")
    parser.add_argument("--dedupe-columns", default="", help="Comma-separated header names used as dedupe key")
    parser.add_argument("--keep-newlines", action="store_true")
    args = parser.parse_args()
    payload = csv_to_xlsx(
        args.csv_path,
        args.xlsx_path or None,
        sheet_name=args.sheet_name,
        dedupe_columns=[item.strip() for item in args.dedupe_columns.split(",")],
        single_line=not args.keep_newlines,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
