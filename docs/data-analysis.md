# Data analysis agent

Checkpoint 09 adds an authenticated, workspace-scoped CSV/XLSX import and deterministic data profile. The API uses Pandas for type inference and numeric calculations, and openpyxl's read-only parser for `.xlsx` workbooks.

## Import and limits

`POST /api/v1/data-analysis/import?workspace_id=...&filename=...` accepts a raw file body (`application/octet-stream`). Owners and members may import; viewers may read stored analysis but cannot upload. Supported formats are UTF-8 CSV and `.xlsx`. Macro-enabled files such as `.xlsm` are rejected.

Limits are 5 MiB per uploaded file, 5,000 data rows, 100 columns, 16,384 characters per cell, 2,000 XLSX archive members, 25 MiB expanded workbook size, and a 500:1 maximum compression ratio. XLSX analysis reads the first visible worksheet. The original upload bytes are not stored.

## Reproducible results

An analysis stores the sanitized filename, original size and SHA-256, parser and analysis versions, row/column counts, per-column missing/distinct counts, inferred type, examples, numeric count/mean/min/max/sum, a deterministic SVG chart for up to 12 numeric columns, and a normalized CSV export. A caller can read the workspace-scoped report using `GET /api/v1/data-analysis/{id}`, download the CSV from `/export.csv`, or fetch a chart by ID from `/charts/{chart_id}.svg`.

The output is reproducible from the stored normalized CSV and analysis version. The original file bytes are intentionally not retained.

## Spreadsheet safety

Spreadsheet formulas are loaded with formula text intact (`data_only=false`) and are treated as ordinary text; neither formulas nor macros execute. Macro-enabled extensions or VBA project entries are rejected. The parser also rejects encrypted, malformed, oversized, suspiciously compressed, and path-traversal ZIP members. CSV output prefixes formula-like text with an apostrophe so spreadsheet software treats it as text when opened. SVG chart labels are XML-escaped.

## Verification

Fixtures cover known statistics, CSV/XLSX parsing, inert formulas, formula-safe exports, XML-escaped chart labels, invalid and oversized files, macro rejection, persisted report/export/chart retrieval, and workspace role isolation. API upload tests use bounded in-memory fixtures.
