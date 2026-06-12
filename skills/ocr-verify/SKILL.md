---
name: ocr-verify
version: 1.0.0
description: "Double-check OCR extraction: cross-verify totals, flag low-confidence data."
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [ocr, verification, data-quality, pdf, excel]
    related_skills: [ocr-and-documents, excel-analyst]
---

# OCR Verification — Double-Check Extracted Data

## When to Use

After OCR extraction from scans, invoices, receipts, or tables.
When data accuracy is critical (finance, legal, compliance).

## Verification Pipeline

### Step 1: Extract with Confidence Scores
```python
import fitz  # pymupdf

doc = fitz.open("document.pdf")
for page in doc:
    blocks = page.get_text("dict")["blocks"]
    for block in blocks:
        if "lines" in block:
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"]
                    confidence = span.get("confidence", 100)
                    if confidence < 95:
                        print(f"⚠️ LOW CONFIDENCE: '{text}' ({confidence}%)")
```

### Step 2: Cross-Verify Totals
```python
# If document has totals/subtotals:
extracted_items = [...]  # Individual line items
extracted_total = ...     # Total from OCR

# Recalculate
calculated_total = sum(extracted_items)

if abs(calculated_total - extracted_total) > 0.01:
    print(f"❌ MISMATCH: OCR={extracted_total}, Calculated={calculated_total}")
    print("Flag for manual review!")
else:
    print("✅ Totals match")
```

### Step 3: Format Validation
```python
import re

# Check dates
def validate_date(text):
    patterns = [
        r'\d{2}\.\d{2}\.\d{4}',  # DD.MM.YYYY
        r'\d{4}-\d{2}-\d{2}',       # YYYY-MM-DD
    ]
    return any(re.match(p, text) for p in patterns)

# Check amounts
def validate_amount(text):
    # Should be numeric with optional decimal
    return bool(re.match(r'^\d+[.,]?\d*$', text.replace(' ', '').replace('$', '').replace('€', '')))
```

### Step 4: Flag Uncertain Data

| Flag | Action |
|------|--------|
| 🟢 High confidence (>98%) | Auto-accept |
| 🟡 Medium (90-98%) | Include but note |
| 🔴 Low (<90%) | Ask user to verify |

### Step 5: Verification Report

```markdown
## OCR Verification Report
- Source: document.pdf (Page 3 of 12)
- Extractor: pymupdf / marker-pdf
- Items extracted: 47
- Confidence: 94.2% average
- ⚠️ Flagged for review: 3 items
  - Line 15: Amount "1.234,56" — ambiguous separator
  - Line 23: Date "03/04/2024" — US vs EU format
  - Line 31: Company name partially garbled
- ✅ Totals verified: match
```

## Rules

1. **Never trust OCR 100%** — always verify totals and key figures
2. **Flag ambiguity** — decimal separators, date formats, similar characters (O/0, l/1)
3. **Cross-reference** — if document has checksums, totals, or references — verify them
4. **Preserve original** — save original scan alongside extracted data
5. **Ask when unsure** — low confidence = human verification required
