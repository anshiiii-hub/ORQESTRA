"""Explicit, deterministic capability registry; no dynamic code loading."""

import os
from dataclasses import dataclass
from typing import Callable

from .agents import inspect_csv, summarize_csv, verify_result
from .contracts import Contract, InspectionOutput, SummaryOutput, VerificationOutput

Handler = Callable[[str, dict], dict]


class RegistryError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AgentDefinition:
    agent_id: str
    capability: str
    handler: Handler
    output_model: type[Contract]
    required_context: tuple[tuple[str, str], ...] = ()
    version: str = "1.0.0"
    schema_version: str = "1.0"
    enabled: bool = True
    allowed_tools: tuple[str, ...] = ()

    def metadata(self) -> dict:
        return {
            "id": self.agent_id,
            "version": self.version,
            "schema_versions": [self.schema_version],
            "capabilities": [self.capability],
            "enabled": self.enabled,
            "allowed_tools": list(self.allowed_tools),
            "required_context": dict(self.required_context),
            "output_schema": self.output_model.model_json_schema(),
        }


class AgentRegistry:
    def __init__(self, definitions: list[AgentDefinition]):
        ids = [definition.agent_id for definition in definitions]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate registered agent ID")
        self._definitions = {definition.agent_id: definition for definition in definitions}

    def all(self) -> tuple[AgentDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def select(
        self,
        capability: str,
        *,
        agent_id: str | None = None,
        schema_version: str = "1.0",
        agent_version: str = "1.0.0",
    ) -> AgentDefinition:
        candidates = [self._definitions[agent_id]] if agent_id in self._definitions else []
        if agent_id is not None and not candidates:
            raise RegistryError("unknown_agent", "Agent is not registered")
        if agent_id is None:
            candidates = list(self.all())
        capable = [agent for agent in candidates if agent.capability == capability]
        if not capable:
            raise RegistryError("capability_mismatch", "No agent provides the required capability")
        enabled = [agent for agent in capable if agent.enabled]
        if not enabled:
            raise RegistryError("disabled_agent", "Required agent is disabled")
        compatible = [
            agent
            for agent in enabled
            if agent.schema_version == schema_version and agent.version == agent_version
        ]
        if not compatible:
            raise RegistryError("incompatible_version", "No compatible agent version is registered")
        return compatible[0]


def build_registry(disabled: tuple[str, ...] = ()) -> AgentRegistry:
    definitions = [
        AgentDefinition("csv_inspector", "csv.inspect", inspect_csv, InspectionOutput),
        AgentDefinition(
            "csv_summarizer",
            "csv.summarize",
            summarize_csv,
            SummaryOutput,
            (("inspect", "csv.inspect"),),
        ),
        AgentDefinition(
            "result_verifier",
            "result.verify",
            verify_result,
            VerificationOutput,
            (("inspect", "csv.inspect"), ("summarize", "csv.summarize")),
        ),
    ]
    from dataclasses import replace

    return AgentRegistry(
        [replace(agent, enabled=agent.agent_id not in disabled) for agent in definitions]
    )


REGISTRY = build_registry(
    tuple(filter(None, (name.strip() for name in os.getenv("DISABLED_AGENTS", "").split(","))))
)
