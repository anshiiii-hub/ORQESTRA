from typing import Literal, Self

from pydantic import Field, model_validator

from .contracts import Capability, Contract, Identifier
from .registry import REGISTRY, RegistryError


class PlannedTask(Contract):
    id: Identifier
    agent: Identifier
    capability: Capability
    agent_version: Literal["1.0.0"] = "1.0.0"
    schema_version: Literal["1.0"] = "1.0"
    dependencies: list[Identifier] = Field(default_factory=list, max_length=25)
    input_bindings: dict[str, Identifier] = Field(default_factory=dict, max_length=25)


class ExecutionPlan(Contract):
    tasks: list[PlannedTask] = Field(min_length=1, max_length=25)

    @model_validator(mode="after")
    def validate_graph(self) -> Self:
        by_name = {task.id: task for task in self.tasks}
        if len(by_name) != len(self.tasks):
            raise ValueError("duplicate task IDs")
        visited: set[str] = set()
        active: set[str] = set()

        def visit(node: str) -> None:
            if node in active:
                raise ValueError("cyclic task dependency")
            if node in visited:
                return
            active.add(node)
            for parent in by_name[node].dependencies:
                if parent not in by_name:
                    raise ValueError("missing dependency")
                visit(parent)
            active.remove(node)
            visited.add(node)

        for name in by_name:
            visit(name)
        for task in self.tasks:
            if len(task.dependencies) != len(set(task.dependencies)):
                raise ValueError("duplicate dependencies")
            try:
                agent = REGISTRY.select(
                    task.capability,
                    agent_id=task.agent,
                    schema_version=task.schema_version,
                    agent_version=task.agent_version,
                )
            except RegistryError as error:
                raise ValueError(str(error)) from None
            required = dict(agent.required_context)
            if set(task.input_bindings) != set(required):
                raise ValueError("input bindings do not match agent requirements")
            for alias, parent in task.input_bindings.items():
                if parent not in task.dependencies or by_name[parent].capability != required[alias]:
                    raise ValueError(
                        "input binding requires a dependency with the correct capability"
                    )
        return self


def demo_plan() -> ExecutionPlan:
    return ExecutionPlan(
        tasks=[
            PlannedTask(id="inspect", agent="csv_inspector", capability="csv.inspect"),
            PlannedTask(
                id="summarize",
                agent="csv_summarizer",
                capability="csv.summarize",
                dependencies=["inspect"],
                input_bindings={"inspect": "inspect"},
            ),
            PlannedTask(
                id="verify",
                agent="result_verifier",
                capability="result.verify",
                dependencies=["inspect", "summarize"],
                input_bindings={"inspect": "inspect", "summarize": "summarize"},
            ),
        ]
    )
