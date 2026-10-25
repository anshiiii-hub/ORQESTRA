import pytest
from pydantic import ValidationError

from app.agents import AgentError, inspect_csv, summarize_csv, verify_result
from app.planner import ExecutionPlan, demo_plan


def test_valid_demo_plan():
    assert len(demo_plan().tasks) == 3


def test_cycle_rejected():
    with pytest.raises(ValidationError):
        ExecutionPlan(
            tasks=[
                {"id": "a", "agent": "x", "dependencies": ["b"]},
                {"id": "b", "agent": "y", "dependencies": ["a"]},
            ]
        )


def test_unknown_dependency_rejected():
    with pytest.raises(ValidationError):
        ExecutionPlan(tasks=[{"id": "a", "agent": "x", "dependencies": ["missing"]}])


def test_demo_chain():
    csv_text = "name,sales\nAlice,10\nBob,20\n"
    inspected = inspect_csv(csv_text, {})
    summarized = summarize_csv(csv_text, {"inspect": inspected})
    checked = verify_result(csv_text, {"inspect": inspected, "summarize": summarized})
    assert checked["passed"] and summarized["numeric_columns"]["sales"]["mean"] == 15


def test_reject_empty_csv():
    with pytest.raises(AgentError):
        inspect_csv("a,b\n", {})
