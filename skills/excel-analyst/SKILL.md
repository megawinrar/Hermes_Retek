---
name: excel-analyst
version: 1.0.0
description: "Advanced Excel/CSV analysis: pivot tables, charts, correlations, anomalies."
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [excel, csv, data-analysis, pandas, charts, analytics]
    related_skills: [ocr-and-documents, jupyter-live-kernel]
---

# Excel / CSV Data Analyst

Advanced data analysis from Excel, CSV, and tabular files.

## Capabilities

### 1. Data Extraction
```python
import pandas as pd

# Excel - all sheets
xls = pd.ExcelFile('file.xlsx')
for sheet in xls.sheet_names:
    df = pd.read_excel(xls, sheet_name=sheet)
    print(f"Sheet: {sheet}, Rows: {len(df)}, Columns: {len(df.columns)}")

# CSV with encoding detection
df = pd.read_csv('file.csv', encoding='utf-8')
# Try encoding='cp1251' for Russian Windows files
```

### 2. Data Profiling
```python
# Full profile
print(df.describe())              # Statistics
print(df.dtypes)                   # Types
print(df.isnull().sum())           # Missing values
print(df.nunique())                # Unique values per column
```

### 3. Analysis Types

| Analysis | When to use | Method |
|----------|-------------|--------|
| **Trend** | Time series | `df.resample('M').sum()` |
| **Correlation** | Find relationships | `df.corr()` + heatmap |
| **Pivot** | Cross-tabulation | `pd.pivot_table()` |
| **Groupby** | Aggregation by category | `df.groupby('col').agg()` |
| **Anomaly** | Outlier detection | Z-score, IQR method |
| **Forecast** | Simple prediction | Moving average, linear trend |

### 4. Visualization
```python
import matplotlib.pyplot as plt
import seaborn as sns

# Correlation heatmap
plt.figure(figsize=(12, 8))
sns.heatmap(df.corr(), annot=True, cmap='coolwarm', center=0)
plt.title('Correlation Matrix')
plt.savefig('correlation.png', dpi=150, bbox_inches='tight')

# Trend line
plt.figure(figsize=(14, 6))
df.resample('M')['value'].sum().plot(kind='line', marker='o')
plt.title('Monthly Trend')
plt.savefig('trend.png', dpi=150, bbox_inches='tight')

# Distribution
plt.figure(figsize=(10, 6))
sns.histplot(df['column'], kde=True)
plt.savefig('distribution.png', dpi=150, bbox_inches='tight')
```

### 5. Export Results
```python
# Multi-sheet Excel report
with pd.ExcelWriter('analysis_report.xlsx') as writer:
    df_summary.to_excel(writer, sheet_name='Summary')
    df_pivot.to_excel(writer, sheet_name='Pivot')
    df_corr.to_excel(writer, sheet_name='Correlations')
```

## OCR Verification for Excel

When data comes from scanned documents:
1. Extract with OCR skill first
2. Load into DataFrame
3. **Verify**: Check totals, cross-reference with source
4. Flag cells with low confidence (< 95%)
5. Ask user to confirm questionable values

## Rules
- Always check data types (dates as strings? numbers as text?)
- Handle missing values explicitly (drop/fill/flag)
- Save intermediate results to `/opt/data/workspace/`
- Generate charts as PNG for Telegram sharing
