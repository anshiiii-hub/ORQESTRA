from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.contracts import AgentRequest, AgentResult, InspectionOutput, NumericStatistics
from app.dispatch import dispatch, validate_result
from app.registry import AgentRegistry, RegistryError, build_registry


def request(**updates):
    body = {
        "schema_version": "1.0",
        "agent_version": "1.0.0",
        "request_id": "request-1",
        "workflow_id": "workflow-1",
        "task_id": "task-1",
        "agent_type": "csv_inspector",
        "capability": "csv.inspect",
        "objective": "Inspect CSV",
        "payload": {"csv_text": "item,sales\nA,10\nB,20"},
    }
    return AgentRequest.model_validate({**body, **updates})


@pytest.mark.parametrize(
    "updates",
    [
        {"schema_version": "2.0"},
        {"agent_version": "2.0.0"},
        {"capability": "shell.exec"},
        {"task_id": "/private/secret"},
        {"input_artifact_ids": ["../../secret"]},
        {"constraints": {"timeout_seconds": 0}},
        {"constraints": {"timeout_seconds": "30"}},
        {"constraints": {"max_tokens": -1}},
        {"constraints": {"max_output_bytes": True}},
        {"untrusted_permission_override": True},
    ],
)
def test_request_rejects_incompatible_or_unsafe_fields(updates):
    with pytest.raises(ValidationError):
        request(**updates)


def test_real_csv_chain_uses_versioned_contracts():
    inspected = dispatch(request())
    assert inspected.status == "succeeded"
    summary_request = request(
        agent_type="csv_summarizer",
        capability="csv.summarize",
        task_id="task-2",
        context_refs={"inspect": "task-1"},
        payload={
            "csv_text": "item,sales\nA,10\nB,20",
            "context": {"inspect": inspected.structured_output},
        },
    )
    summarized = dispatch(summary_request)
    verified = dispatch(
        request(
            agent_type="result_verifier",
            capability="result.verify",
            task_id="task-3",
            context_refs={"inspect": "task-1", "summarize": "task-2"},
            payload={
                "csv_text": "item,sales\nA,10\nB,20",
                "context": {
                    "inspect": inspected.structured_output,
                    "summarize": summarized.structured_output,
                },
            },
        )
    )
    assert summarized.structured_output["numeric_columns"]["sales"]["sum"] == 30
    assert verified.status == "succeeded" and verified.structured_output["passed"] is True
    assert verified.usage.cost_usd == 0
    assert AgentResult.model_validate_json(verified.model_dump_json()) == verified


@pytest.mark.parametrize(
    "capability,agent_id,code",
    [
        ("csv.inspect", "unknown", "unknown_agent"),
        ("csv.inspect", "csv_summarizer", "capability_mismatch"),
        ("unknown.capability", None, "capability_mismatch"),
    ],
)
def test_missing_capability_never_selects_an_agent(capability, agent_id, code):
    with pytest.raises(RegistryError) as error:
        build_registry().select(capability, agent_id=agent_id)
    assert error.value.code == code


def test_registry_rejects_disabled_duplicate_and_incompatible_agents():
    registry = build_registry(("csv_inspector",))
    result = dispatch(request(), registry)
    assert result.error.code == "disabled_agent"
    with pytest.raises(RegistryError, match="compatible"):
        build_registry().select("csv.inspect", schema_version="2.0")
    definition = build_registry().all()[0]
    with pytest.raises(ValueError, match="Duplicate"):
        AgentRegistry([definition, definition])


def test_permission_scope_cannot_be_extended_by_request():
    result = dispatch(
        request(tool_permissions=["shell.exec"]), effective_permissions=frozenset({"shell.exec"})
    )
    assert result.error.code == "permission_denied"
    assert result.structured_output is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"row_count": True, "columns": ["a"], "rows": [{"a": "1"}]},
        {"row_count": 1, "columns": ["a"], "rows": [{"b": "1"}]},
        {"row_count": 2, "columns": ["a"], "rows": [{"a": "1"}]},
        {"row_count": 1, "columns": ["a"], "rows": [{"a": "1"}], "extra": "forbidden"},
    ],
)
def test_invalid_agent_outputs_never_succeed(payload):
    definition = build_registry().select("csv.inspect")
    registry = AgentRegistry([replace(definition, handler=lambda *_: payload)])
    result = dispatch(request(), registry)
    assert result.status == "failed" and result.error.code == "invalid_result"
    assert result.structured_output is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), "10", True])
def test_numeric_contract_requires_finite_numbers(value):
    with pytest.raises(ValidationError):
        NumericStatistics(count=1, sum=value, mean=1.0, min=1.0, max=1.0)


def test_response_identity_cannot_be_substituted():
    original = request()
    result = dispatch(original).model_dump()
    for key in ["task_id", "workflow_id", "request_id", "agent_type"]:
        with pytest.raises(ValueError):
            validate_result(original, {**result, key: "different-id"})


def test_failed_output_invariants():
    result = dispatch(request()).model_dump()
    with pytest.raises(ValidationError):
        AgentResult.model_validate({**result, "status": "failed"})
    with pytest.raises(ValidationError):
        AgentResult.model_validate({**result, "structured_output": None})
    with pytest.raises(ValidationError):
        AgentResult.model_validate({**result, "structured_output": {"nested": [float("inf")]}})


def test_secret_exception_messages_are_not_published():
    def broken(*_):
        raise RuntimeError("sk-secret-token /private/user/file.csv")

    definition = build_registry().select("csv.inspect")
    result = dispatch(request(), AgentRegistry([replace(definition, handler=broken)]))
    assert result.error.code == "execution_failed"
    assert "sk-secret-token" not in result.model_dump_json()
    assert "/private" not in result.model_dump_json()


def test_deadline_and_output_size_limits():
    times = iter([0.0, 31.0])
    assert dispatch(request(), clock=lambda: next(times)).error.code == "deadline_exceeded"
    assert (
        dispatch(request(constraints={"max_output_bytes": 1})).error.code == "output_limit_exceeded"
    )


def test_request_context_is_limited_to_required_inputs():
    assert dispatch(request(context_refs={"inspect": "parent"})).error.code == "invalid_input"
    assert dispatch(request(input_artifact_ids=["artifact-1"])).error.code == "invalid_input"
    inspected = InspectionOutput(row_count=1, columns=["a"], rows=[{"a": "1"}]).model_dump()
    result = dispatch(request(payload={"csv_text": "a\n1", "context": {"inspect": inspected}}))
    assert result.error.code == "invalid_input"


def test_committed_schemas_match_runtime_models():
    import json

    from app.export_contracts import SCHEMA_DIRECTORY, schemas

    for name, schema in schemas().items():
        assert json.loads((SCHEMA_DIRECTORY / name).read_text()) == schema


@pytest.mark.parametrize("passed", [1, "true", False])
def test_verification_flag_requires_literal_boolean_true(passed):
    from app.contracts import VerificationOutput

    with pytest.raises(ValidationError):
        VerificationOutput.model_validate(
            {
                "passed": passed,
                "checks": ["check"],
                "summary": {"row_count": 1, "columns": ["a"], "numeric_columns": {}},
            }
        )


def test_version_tags_are_required_on_wire_messages():
    original = request().model_dump()
    for key in ["schema_version", "agent_version"]:
        with pytest.raises(ValidationError):
            AgentRequest.model_validate(
                {name: value for name, value in original.items() if name != key}
            )
        result = dispatch(request()).model_dump()
        with pytest.raises(ValidationError):
            AgentResult.model_validate(
                {name: value for name, value in result.items() if name != key}
            )


def test_unknown_disabled_agent_configuration_is_rejected():
    with pytest.raises(ValueError, match="unregistered"):
        build_registry(("typo-agent",))


def test_result_must_preserve_all_parent_references():
    inspected = dispatch(request())
    summary_request = request(
        agent_type="csv_summarizer",
        capability="csv.summarize",
        task_id="task-2",
        context_refs={"inspect": "task-1"},
        payload={
            "csv_text": "item,sales\nA,10\nB,20",
            "context": {"inspect": inspected.structured_output},
        },
    )
    result = dispatch(summary_request).model_dump()
    for refs in [[], ["task-1", "task-1"], ["foreign-task"]]:
        with pytest.raises(ValueError):
            validate_result(summary_request, {**result, "source_refs": refs})
