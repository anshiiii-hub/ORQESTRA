"""Validate dispatch permissions, inputs and outputs at the agent boundary."""

import json
from time import monotonic
from typing import Callable

from pydantic import ValidationError

from .contracts import AgentFailure, AgentRequest, AgentResult, Usage
from .registry import REGISTRY, AgentRegistry, RegistryError


def failed_result(request: AgentRequest, code: str, stage: str, message: str) -> AgentResult:
    return AgentResult(
        request_id=request.request_id,
        workflow_id=request.workflow_id,
        task_id=request.task_id,
        agent_type=request.agent_type,
        agent_version=request.agent_version,
        status="failed",
        summary="Agent task failed",
        error=AgentFailure.model_validate(
            {"code": code, "stage": stage, "message": message, "retryable": False}
        ),
    )


def validate_result(
    request: AgentRequest, result: AgentResult | dict, registry: AgentRegistry = REGISTRY
) -> AgentResult:
    # Revalidate model instances too: callers can mutate nested dictionaries after construction.
    parsed = AgentResult.model_validate(
        result.model_dump() if isinstance(result, AgentResult) else result
    )
    for key in (
        "request_id",
        "workflow_id",
        "task_id",
        "agent_type",
        "agent_version",
        "schema_version",
    ):
        if getattr(parsed, key) != getattr(request, key):
            raise ValueError("Agent result does not match its request")
    if parsed.artifact_ids or not set(parsed.source_refs).issubset(request.context_refs.values()):
        raise ValueError("Unrecognized artifact or source reference")
    if parsed.status == "succeeded":
        agent = registry.select(
            request.capability,
            agent_id=request.agent_type,
            schema_version=request.schema_version,
            agent_version=request.agent_version,
        )
        output = agent.output_model.model_validate(parsed.structured_output)
        parsed.structured_output = output.model_dump(mode="json")
    return parsed


def dispatch(
    request: AgentRequest,
    registry: AgentRegistry = REGISTRY,
    effective_permissions: frozenset[str] = frozenset(),
    clock: Callable[[], float] = monotonic,
) -> AgentResult:
    request = AgentRequest.model_validate(request.model_dump())
    try:
        agent = registry.select(
            request.capability,
            agent_id=request.agent_type,
            schema_version=request.schema_version,
            agent_version=request.agent_version,
        )
    except RegistryError as error:
        return failed_result(request, error.code, "dispatch", str(error))
    if not set(request.tool_permissions).issubset(set(agent.allowed_tools) & effective_permissions):
        return failed_result(
            request, "permission_denied", "dispatch", "Requested tools are not permitted"
        )
    context = request.payload.context.model_dump(mode="json", exclude_none=True)
    expected_keys = set(dict(agent.required_context))
    if (
        set(context) != expected_keys
        or set(request.context_refs) != expected_keys
        or request.task_id in request.context_refs.values()
        or request.input_artifact_ids
    ):
        return failed_result(
            request, "invalid_input", "input", "Agent context does not match its input contract"
        )
    started = clock()
    try:
        output = agent.handler(request.payload.csv_text, context)
    except Exception:
        # Exception messages can contain input data, credentials or host paths.
        return failed_result(
            request,
            "execution_failed",
            "execution",
            "Agent could not process the supplied CSV data",
        )
    if clock() - started > request.constraints.timeout_seconds:
        return failed_result(
            request, "deadline_exceeded", "execution", "Agent exceeded its execution deadline"
        )
    try:
        result = AgentResult(
            request_id=request.request_id,
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            agent_type=agent.agent_id,
            agent_version=request.agent_version,
            status="succeeded",
            summary=f"{request.capability} completed",
            structured_output=output,
            source_refs=list(dict.fromkeys(request.context_refs.values())),
            usage=Usage(input_tokens=0, output_tokens=0, cost_usd=0.0),
        )
        result = validate_result(request, result, registry)
        if (
            len(json.dumps(result.model_dump(mode="json"), allow_nan=False).encode())
            > request.constraints.max_output_bytes
        ):
            return failed_result(
                request, "output_limit_exceeded", "output", "Agent output exceeded its size limit"
            )
        return result
    except (ValidationError, ValueError, TypeError, OverflowError):
        return failed_result(
            request, "invalid_result", "output", "Agent output did not satisfy its contract"
        )
