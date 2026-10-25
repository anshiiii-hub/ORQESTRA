"""Version 1.0 messages shared by the planner, dispatcher and deterministic agents."""

import json
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")]
Capability = Literal["csv.inspect", "csv.summarize", "result.verify"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Constraints(Contract):
    max_tokens: int | None = Field(default=None, ge=0, le=100000)
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_output_bytes: int = Field(default=1000000, ge=1, le=2000000)


class InspectionOutput(Contract):
    row_count: int = Field(ge=1, le=1000)
    columns: list[str] = Field(min_length=1, max_length=40)
    rows: list[dict[str, str | None]] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def consistent_rows(self) -> Self:
        if len(self.columns) != len(set(self.columns)) or len(self.rows) != self.row_count:
            raise ValueError("Inconsistent CSV shape")
        if any(set(row) != set(self.columns) for row in self.rows):
            raise ValueError("CSV row keys do not match columns")
        return self


class NumericStatistics(Contract):
    count: int = Field(ge=1, le=1000)
    sum: float
    mean: float
    min: float
    max: float

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if not self.min <= self.mean <= self.max:
            raise ValueError("Numeric mean is outside its range")
        return self


class SummaryOutput(Contract):
    row_count: int = Field(ge=1, le=1000)
    columns: list[str] = Field(min_length=1, max_length=40)
    numeric_columns: dict[str, NumericStatistics]

    @model_validator(mode="after")
    def valid_columns(self) -> Self:
        if len(self.columns) != len(set(self.columns)):
            raise ValueError("Duplicate columns")
        if not set(self.numeric_columns).issubset(self.columns):
            raise ValueError("Unknown numeric column")
        if any(stats.count > self.row_count for stats in self.numeric_columns.values()):
            raise ValueError("Numeric count exceeds row count")
        return self


class VerificationOutput(Contract):
    passed: Literal[True]
    checks: list[str] = Field(min_length=1, max_length=20)
    summary: SummaryOutput


class TaskContext(Contract):
    inspect: InspectionOutput | None = None
    summarize: SummaryOutput | None = None


class CSVInput(Contract):
    csv_text: str = Field(min_length=3, max_length=250000)
    context: TaskContext = Field(default_factory=TaskContext)


class AgentRequest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    request_id: Identifier
    workflow_id: Identifier
    task_id: Identifier
    agent_type: Identifier
    agent_version: Literal["1.0.0"] = "1.0.0"
    capability: Capability
    objective: str = Field(min_length=1, max_length=3000)
    input_artifact_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    context_refs: dict[str, Identifier] = Field(default_factory=dict, max_length=25)
    constraints: Constraints = Field(default_factory=Constraints)
    tool_permissions: list[Identifier] = Field(default_factory=list, max_length=20)
    payload: CSVInput


class Usage(Contract):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class AgentFailure(Contract):
    code: Literal[
        "unknown_agent",
        "disabled_agent",
        "capability_mismatch",
        "incompatible_version",
        "permission_denied",
        "invalid_input",
        "invalid_result",
        "execution_failed",
        "deadline_exceeded",
        "output_limit_exceeded",
    ]
    retryable: bool = False
    stage: Literal["dispatch", "input", "execution", "output"]
    message: str = Field(min_length=1, max_length=300)


class AgentResult(Contract):
    schema_version: Literal["1.0"] = "1.0"
    request_id: Identifier
    workflow_id: Identifier
    task_id: Identifier
    agent_type: Identifier
    agent_version: Literal["1.0.0"] = "1.0.0"
    status: Literal["succeeded", "failed"]
    summary: str = Field(min_length=1, max_length=1000)
    structured_output: dict[str, JsonValue] | None = None
    artifact_ids: list[Identifier] = Field(default_factory=list, max_length=20)
    source_refs: list[Identifier] = Field(default_factory=list, max_length=25)
    usage: Usage = Field(default_factory=Usage)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    error: AgentFailure | None = None

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if self.status == "succeeded" and (
            self.error is not None or self.structured_output is None
        ):
            raise ValueError("Successful results need output and no error")
        if self.status == "failed" and (
            self.error is None or self.structured_output is not None or self.artifact_ids
        ):
            raise ValueError("Failed results need an error and cannot publish outputs")
        # JsonValue accepts nested containers; reject nonfinite values at every depth.
        json.dumps(self.model_dump(), allow_nan=False)
        return self
