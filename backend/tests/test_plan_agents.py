import pytest
from pydantic import ValidationError

from app.agents import AgentError, inspect_csv, summarize_csv, verify_result
from app.planner import ExecutionPlan, demo_plan


def test_valid_demo_plan():
    assert len(demo_plan().tasks) == 3


def test_cycle_rejected():
    with pytest.raises(ValidationError, match="cyclic"):
        ExecutionPlan(
            tasks=[
                {
                    "id": "a",
                    "agent": "csv_inspector",
                    "capability": "csv.inspect",
                    "dependencies": ["b"],
                },
                {
                    "id": "b",
                    "agent": "csv_inspector",
                    "capability": "csv.inspect",
                    "dependencies": ["a"],
                },
            ]
        )


def test_unknown_dependency_rejected():
    with pytest.raises(ValidationError, match="missing dependency"):
        ExecutionPlan(
            tasks=[
                {
                    "id": "a",
                    "agent": "csv_inspector",
                    "capability": "csv.inspect",
                    "dependencies": ["missing"],
                }
            ]
        )


def test_demo_chain():
    csv_text = "name,sales\nAlice,10\nBob,20\n"
    inspected = inspect_csv(csv_text, {})
    summarized = summarize_csv(csv_text, {"inspect": inspected})
    checked = verify_result(csv_text, {"inspect": inspected, "summarize": summarized})
    assert checked["passed"] and summarized["numeric_columns"]["sales"]["mean"] == 15


def test_reject_empty_csv():
    with pytest.raises(AgentError):
        inspect_csv("a,b\n", {})


@pytest.mark.parametrize(
    "field,value",
    [
        ("agent", "unregistered"),
        ("capability", "csv.inspect"),
        ("schema_version", "2.0"),
        ("agent_version", "2.0.0"),
        ("input_bindings", {}),
        ("input_bindings", {"inspect": "verify"}),
        ("dependencies", ["inspect", "inspect"]),
        ("tool_permissions", ["shell.exec"]),
    ],
)
def test_plan_rejects_incompatible_agents_and_invalid_bindings(field, value):
    body = demo_plan().model_dump()
    body["tasks"][1][field] = value
    with pytest.raises(ValidationError):
        ExecutionPlan.model_validate(body)


def test_plan_ids_can_differ_from_agent_roles():
    body = demo_plan().model_dump()
    mapping = {"inspect": "task-a", "summarize": "task-b", "verify": "task-c"}
    for task in body["tasks"]:
        task["id"] = mapping[task["id"]]
        task["dependencies"] = [mapping[parent] for parent in task["dependencies"]]
        task["input_bindings"] = {
            alias: mapping[parent] for alias, parent in task["input_bindings"].items()
        }
    assert ExecutionPlan.model_validate(body).tasks[-1].id == "task-c"


def test_duplicate_plan_ids_are_rejected():
    body = demo_plan().model_dump()
    body["tasks"][1]["id"] = body["tasks"][0]["id"]
    with pytest.raises(ValidationError, match="duplicate task IDs"):
        ExecutionPlan.model_validate(body)
