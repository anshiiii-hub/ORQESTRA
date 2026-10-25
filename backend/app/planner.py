from pydantic import BaseModel, Field, model_validator


class PlannedTask(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    agent: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)


class ExecutionPlan(BaseModel):
    tasks: list[PlannedTask] = Field(min_length=1, max_length=25)

    @model_validator(mode="after")
    def validate_graph(self):
        ids = [t.id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate task IDs")
        refs = {t.id: t.dependencies for t in self.tasks}
        visited, active = set(), set()

        def visit(node):
            if node in active:
                raise ValueError("cyclic task dependency")
            if node in visited:
                return
            active.add(node)
            for dep in refs[node]:
                if dep not in refs:
                    raise ValueError(f"missing dependency {dep}")
                visit(dep)
            active.remove(node)
            visited.add(node)

        for i in ids:
            visit(i)
        return self


def demo_plan() -> ExecutionPlan:
    # Checkpoint 1 deliberately uses a deterministic plan. LLM planning comes later.
    return ExecutionPlan(
        tasks=[
            PlannedTask(id="inspect", agent="csv_inspector"),
            PlannedTask(id="summarize", agent="csv_summarizer", dependencies=["inspect"]),
            PlannedTask(id="verify", agent="result_verifier", dependencies=["summarize"]),
        ]
    )
