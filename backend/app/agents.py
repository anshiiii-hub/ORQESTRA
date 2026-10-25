import csv
import io
import math
from decimal import Decimal, InvalidOperation
from typing import Any


class AgentError(Exception):
    pass


def inspect_csv(csv_text: str, inputs: dict[str, Any]) -> dict:
    try:
        reader = csv.DictReader(io.StringIO(csv_text))
        headers = reader.fieldnames
        if not headers or len(headers) > 40 or len(set(headers)) != len(headers):
            raise AgentError("CSV needs 1–40 unique column headers")
        rows: list[dict] = []
        for row in reader:
            if len(rows) >= 1000:
                raise AgentError("Demo limit: at most 1000 rows")
            if None in row:
                raise AgentError("CSV row has more values than headers")
            rows.append(row)
        if not rows:
            raise AgentError("CSV has no data rows")
        return {"row_count": len(rows), "columns": headers, "rows": rows}
    except csv.Error as e:
        raise AgentError(f"Invalid CSV: {e}") from e


def summarize_csv(csv_text: str, inputs: dict[str, Any]) -> dict:
    inspection = inputs["inspect"]
    result = {
        "row_count": inspection["row_count"],
        "columns": inspection["columns"],
        "numeric_columns": {},
    }
    for column in inspection["columns"]:
        vals: list[Decimal] = []
        for row in inspection["rows"]:
            raw = (row.get(column) or "").strip()
            if not raw:
                continue
            try:
                dec = Decimal(raw)
            except (InvalidOperation, ValueError):
                vals = []
                break
            if not dec.is_finite() or not math.isfinite(float(dec)):
                raise AgentError(
                    f"Numeric value in column {column!r} is outside the supported finite range"
                )
            vals.append(dec)
        if vals:
            total = sum(vals)
            stats = {
                "count": len(vals),
                "sum": float(total),
                "mean": float(total / len(vals)),
                "min": float(min(vals)),
                "max": float(max(vals)),
            }
            if not all(math.isfinite(value) for value in stats.values()):
                raise AgentError(
                    f"Numeric summary for column {column!r} is outside the supported finite range"
                )
            result["numeric_columns"][column] = stats
    return result


def verify_result(csv_text: str, inputs: dict[str, Any]) -> dict:
    # Re-read the source and calculate expectations independently of upstream outputs.
    source = inspect_csv(csv_text, {})
    summary = inputs["summarize"]
    expected = {
        "row_count": source["row_count"],
        "columns": source["columns"],
        "numeric_columns": {},
    }
    for column in source["columns"]:
        values: list[Decimal] = []
        for row in source["rows"]:
            raw = (row.get(column) or "").strip()
            if not raw:
                continue
            try:
                value = Decimal(raw)
            except (InvalidOperation, ValueError):
                values = []
                break
            if not value.is_finite() or not math.isfinite(float(value)):
                raise AgentError("Verification failed: nonfinite source value")
            values.append(value)
        if values:
            total = sum(values)
            stats = dict(
                count=len(values),
                sum=float(total),
                mean=float(total / len(values)),
                min=float(min(values)),
                max=float(max(values)),
            )
            if not all(math.isfinite(value) for value in stats.values()):
                raise AgentError("Verification failed: nonfinite numeric summary")
            expected["numeric_columns"][column] = stats
    if summary != expected:
        raise AgentError("Verification failed: summary does not match source CSV")
    return {
        "passed": True,
        "checks": [
            "row count matches source",
            "column schema matches source",
            "numeric columns match source",
            "numeric counts, sums, means and ranges recalculated",
        ],
        "summary": summary,
    }
