import csv
import io
import math
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

class AgentError(Exception):
    pass

def inspect_csv(csv_text: str, inputs: dict[str, Any]) -> dict:
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        headers = reader.fieldnames
        if not headers or len(headers) > 40 or len(set(headers)) != len(headers):
            raise AgentError("CSV needs 1–40 unique column headers")
        rows = []
        for row in reader:
            if len(rows) >= 1000: raise AgentError("Demo limit: at most 1000 rows")
            if None in row: raise AgentError("CSV row has more values than headers")
            rows.append(row)
        if not rows: raise AgentError("CSV has no data rows")
        return {"row_count": len(rows), "columns": headers, "rows": rows}
    except csv.Error as e:
        raise AgentError(f"Invalid CSV: {e}") from e

def summarize_csv(csv_text: str, inputs: dict[str, Any]) -> dict:
    inspection = inputs["inspect"]
    result = {"row_count": inspection["row_count"], "columns": inspection["columns"], "numeric_columns": {}}
    for column in inspection["columns"]:
        vals = []
        for row in inspection["rows"]:
            raw = (row.get(column) or "").strip()
            if not raw: continue
            try:
                dec = Decimal(raw)
            except (InvalidOperation, ValueError):
                vals = []
                break
            if not dec.is_finite() or not math.isfinite(float(dec)):
                raise AgentError(f"Numeric value in column {column!r} is outside the supported finite range")
            vals.append(dec)
        if vals:
            total = sum(vals)
            stats = {
                "count": len(vals), "sum": float(total),
                "mean": float(total / len(vals)),
                "min": float(min(vals)), "max": float(max(vals)),
            }
            if not all(math.isfinite(value) for value in stats.values()):
                raise AgentError(f"Numeric summary for column {column!r} is outside the supported finite range")
            result["numeric_columns"][column] = stats
    return result

def verify_result(csv_text: str, inputs: dict[str, Any]) -> dict:
    inspected, summary = inputs["inspect"], inputs["summarize"]
    passed = inspected["row_count"] == summary["row_count"] and inspected["columns"] == summary["columns"]
    for col, stats in summary["numeric_columns"].items():
        if not (stats["min"] <= stats["mean"] <= stats["max"]): passed = False
    if not passed: raise AgentError("Verification failed")
    return {"passed": True, "checks": ["row count matches", "column schema matches", "numeric ranges valid"], "summary": summary}

AGENTS: dict[str, Callable] = {"csv_inspector": inspect_csv, "csv_summarizer": summarize_csv, "result_verifier": verify_result}
