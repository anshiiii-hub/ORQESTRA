import pytest

from app.agents import AgentError, inspect_csv, summarize_csv, verify_result


CSV = "item,sales\nA,10\nB,20\nC,\n"


def outputs():
    inspection = inspect_csv(CSV, {})
    return {"inspect": inspection, "summarize": summarize_csv(CSV, {"inspect": inspection})}


@pytest.mark.parametrize("field,value", [("count", 9), ("sum", 999999), ("mean", 16), ("min", 0), ("max", 100)])
def test_verifier_rejects_incorrect_statistics(field, value):
    inputs = outputs()
    inputs["summarize"]["numeric_columns"]["sales"][field] = value
    with pytest.raises(AgentError, match="Verification failed"):
        verify_result(CSV, inputs)


def test_verifier_rejects_missing_numeric_column():
    inputs = outputs()
    inputs["summarize"]["numeric_columns"].clear()
    with pytest.raises(AgentError, match="Verification failed"):
        verify_result(CSV, inputs)


def test_verifier_checks_source_even_when_upstream_counts_agree():
    inputs = outputs()
    inputs["inspect"]["row_count"] = inputs["summarize"]["row_count"] = 100
    with pytest.raises(AgentError, match="Verification failed"):
        verify_result(CSV, inputs)


def test_verifier_accepts_correct_summary_with_missing_numeric_cells():
    assert verify_result(CSV, outputs())["passed"] is True
