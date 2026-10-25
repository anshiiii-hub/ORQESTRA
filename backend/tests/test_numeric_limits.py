import json

import pytest

from app.agents import AgentError, inspect_csv, summarize_csv


@pytest.mark.parametrize("values", ["1e309", "-1e309", "NaN", "Infinity", "1e308\n1e308"])
def test_nonfinite_values_and_totals_are_rejected(values):
    csv_text = f"value\n{values}"
    with pytest.raises(AgentError, match="finite range"):
        summarize_csv(csv_text, {"inspect": inspect_csv(csv_text, {})})


def test_finite_summary_is_json_serializable():
    csv_text = "item,value\nA,-10.5\nB,20\nC,\n"
    summary = summarize_csv(csv_text, {"inspect": inspect_csv(csv_text, {})})
    assert summary["numeric_columns"]["value"] == {
        "count": 2, "sum": 9.5, "mean": 4.75, "min": -10.5, "max": 20.0,
    }
    json.dumps(summary, allow_nan=False)
