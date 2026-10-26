"""Run reproducible in-process checks through the registered deterministic agents."""

import json
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contracts import AgentRequest
from .dispatch import dispatch
from .models import BenchmarkCase, BenchmarkResult, BenchmarkSet
from .planner import demo_plan
from .registry import REGISTRY
from .routing import select_route


def seed_default_benchmarks(db: Session) -> BenchmarkSet:
    """Ensure the standard fixture set exists before an explicit benchmark run."""
    benchmark_set = db.scalar(
        select(BenchmarkSet).where(BenchmarkSet.name == "Standard Orchestration Suite v1")
    )
    if benchmark_set is None:
        benchmark_set = BenchmarkSet(
            name="Standard Orchestration Suite v1",
            version="1.0.0",
            description=(
                "Small deterministic smoke suite for the registered CSV agent pipeline. "
                "This is not a statistical performance or security benchmark."
            ),
        )
        db.add(benchmark_set)
        db.flush()
        cases = [
            BenchmarkCase(
                set_id=benchmark_set.id,
                case_key="csv_statistics_fixed",
                prompt="Analyze the CSV and verify the revenue statistics",
                input_data={
                    "csv": "product,revenue,units\nWidget,1200,10\nGadget,2500,25\nDoohickey,800,8"
                },
                expected_output_pattern="revenue",
                routing_mode="fixed",
                is_active=True,
            ),
            BenchmarkCase(
                set_id=benchmark_set.id,
                case_key="csv_statistics_adaptive",
                prompt="Analyze the CSV and verify the revenue statistics",
                input_data={
                    "csv": "product,revenue,units\nWidget,1200,10\nGadget,2500,25\nDoohickey,800,8"
                },
                expected_output_pattern="revenue",
                routing_mode="adaptive",
                is_active=True,
            ),
            BenchmarkCase(
                set_id=benchmark_set.id,
                case_key="numeric_verification_fixed",
                prompt="Verify the numeric summary for the inventory dataset",
                input_data={"csv": "item,count,cost\nBolt,100,5\nNut,200,2"},
                expected_output_pattern="count",
                routing_mode="fixed",
                is_active=True,
            ),
            BenchmarkCase(
                set_id=benchmark_set.id,
                case_key="numeric_verification_adaptive",
                prompt="Verify the numeric summary for the inventory dataset",
                input_data={"csv": "item,count,cost\nBolt,100,5\nNut,200,2"},
                expected_output_pattern="count",
                routing_mode="adaptive",
                is_active=True,
            ),
        ]
        db.add_all(cases)
        db.commit()
        db.refresh(benchmark_set)
    return benchmark_set


def execute_benchmark_case(
    db: Session,
    case: BenchmarkCase,
    execution_id: str,
    routing_mode_override: str | None = None,
) -> BenchmarkResult:
    """Run the demo DAG through normal routing and dispatch, then persist measured results."""
    mode = routing_mode_override or case.routing_mode
    csv_text = case.input_data.get("csv", "")
    plan = demo_plan()
    outputs: dict[str, dict[str, Any]] = {}
    agents_used: list[str] = []
    total_tokens = 0
    total_cost = 0.0
    workflow_id = str(uuid4())
    started = time.perf_counter()
    failure_code: str | None = None

    for task in plan.tasks:
        required_context = {
            alias: REGISTRY.select(
                plan_task.capability,
                agent_id=plan_task.agent,
                schema_version=plan_task.schema_version,
                agent_version=plan_task.agent_version,
            ).capability
            for alias, parent_name in task.input_bindings.items()
            for plan_task in plan.tasks
            if plan_task.id == parent_name
        }
        selected, _decision = select_route(
            mode=mode,
            requested_agent=task.agent,
            capability=task.capability,
            schema_version=task.schema_version,
            agent_version=task.agent_version,
            required_context=required_context,
            registry=REGISTRY,
            histories={},
            # The suite invokes agents in-process; this represents its one local runner.
            worker_counts={agent.agent_id: 1 for agent in REGISTRY.all()},
        )
        context = {
            alias: outputs[parent]
            for alias, parent in task.input_bindings.items()
            if parent in outputs
        }
        if len(context) != len(task.input_bindings):
            failure_code = "benchmark_dependency_missing"
            break
        request = AgentRequest.model_validate(
            {
                "schema_version": task.schema_version,
                "request_id": str(uuid4()),
                "workflow_id": workflow_id,
                "task_id": task.id,
                "agent_type": selected.agent_id,
                "agent_version": selected.version,
                "capability": task.capability,
                "objective": case.prompt,
                "context_refs": {alias: parent for alias, parent in task.input_bindings.items()},
                "payload": {"csv_text": csv_text, "context": context},
                "tool_permissions": [],
            }
        )
        agent_result = dispatch(request)
        if agent_result.status != "succeeded" or agent_result.structured_output is None:
            failure_code = (
                agent_result.error.code if agent_result.error else "benchmark_agent_failed"
            )
            break
        outputs[task.id] = agent_result.structured_output
        agents_used.append(selected.agent_id)
        total_tokens += (agent_result.usage.input_tokens or 0) + (
            agent_result.usage.output_tokens or 0
        )
        total_cost += agent_result.usage.cost_usd or 0.0

    elapsed_ms = max(1, round((time.perf_counter() - started) * 1000))
    final_output = outputs.get(plan.tasks[-1].id, {})
    pattern_matched = (
        case.expected_output_pattern.casefold()
        in json.dumps(final_output, sort_keys=True).casefold()
    )
    score = 1.0 if failure_code is None and pattern_matched else 0.0
    status = "succeeded" if score == 1.0 else "failed"

    benchmark_result = BenchmarkResult(
        benchmark_case_id=case.id,
        execution_id=execution_id,
        routing_mode=mode,
        status=status,
        duration_ms=elapsed_ms,
        tokens_used=total_tokens,
        cost_usd=total_cost,
        score=score,
        benchmark_metadata={
            "case_key": case.case_key,
            "tasks_executed": len(agents_used),
            "agents_used": agents_used,
            "expected_output_matched": pattern_matched,
            "failure_code": failure_code,
            "execution_kind": "in_process_deterministic_dispatch",
        },
    )
    db.add(benchmark_result)
    db.commit()
    db.refresh(benchmark_result)
    return benchmark_result


def execute_benchmark_suite(
    db: Session,
    set_id: str | None = None,
    routing_mode_override: str | None = None,
) -> list[BenchmarkResult]:
    """Execute all active cases in the standard set and persist their results."""
    benchmark_set = seed_default_benchmarks(db)
    target_set_id = set_id or benchmark_set.id
    cases = list(
        db.scalars(
            select(BenchmarkCase)
            .where(BenchmarkCase.set_id == target_set_id, BenchmarkCase.is_active.is_(True))
            .order_by(BenchmarkCase.created_at.asc(), BenchmarkCase.case_key.asc())
        ).all()
    )
    execution_id = str(uuid4())
    return [execute_benchmark_case(db, case, execution_id, routing_mode_override) for case in cases]


def get_benchmark_summary(db: Session) -> dict[str, Any]:
    """Aggregate recorded runs without creating data or implying unmeasured speedups."""
    results = list(db.scalars(select(BenchmarkResult)).all())
    by_mode: dict[str, list[BenchmarkResult]] = {"fixed": [], "adaptive": []}
    for result in results:
        by_mode.setdefault(result.routing_mode, []).append(result)

    def stats_for(items: list[BenchmarkResult]) -> dict[str, Any]:
        if not items:
            return {
                "runs": 0,
                "success_rate": 0.0,
                "mean_duration_ms": 0.0,
                "mean_score": 0.0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
            }
        succeeded = [item for item in items if item.status == "succeeded"]
        return {
            "runs": len(items),
            "success_rate": round(len(succeeded) / len(items), 3),
            "mean_duration_ms": round(sum(item.duration_ms for item in items) / len(items), 1),
            "mean_score": round(sum(item.score for item in items) / len(items), 3),
            "total_tokens": sum(item.tokens_used for item in items),
            "total_cost_usd": round(sum(item.cost_usd for item in items), 6),
        }

    return {
        "suite_name": "Standard Orchestration Suite v1",
        "total_executions": len({result.execution_id for result in results}),
        "baselines": {
            "fixed": stats_for(by_mode.get("fixed", [])),
            "adaptive": stats_for(by_mode.get("adaptive", [])),
        },
        "parallel_speedup": None,
        "evaluation_notes": (
            "Runs the registered deterministic CSV agent DAG in-process. The built-in registry "
            "has one agent per capability, so fixed and adaptive choose the same agents. "
            "Latency is local sequential dispatch time; parallel speedup and provider cost are "
            "not measured by this smoke suite."
        ),
    }
