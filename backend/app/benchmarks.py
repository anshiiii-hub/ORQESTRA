"""Frozen, deterministic evaluation suite for the registered CSV agent pipeline."""

import hashlib
import json
import math
import time
from functools import lru_cache
from pathlib import Path
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

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "benchmark_v2.json"
SUITE_NAME = "ORQESTRA Deterministic Orchestration Evaluation v2"
ALLOWED_MODES = ("single", "fixed", "adaptive")


@lru_cache(maxsize=1)
def load_fixture() -> tuple[dict[str, Any], str]:
    raw = FIXTURE_PATH.read_bytes()
    dataset = json.loads(raw)
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not 50 <= len(cases) <= 100:
        raise ValueError("Benchmark fixture must contain between 50 and 100 cases")
    keys = [case.get("case_id") for case in cases]
    if any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)):
        raise ValueError("Benchmark case identifiers must be unique strings")
    return dataset, hashlib.sha256(raw).hexdigest()


def seed_evaluation_set(db: Session) -> BenchmarkSet:
    dataset, digest = load_fixture()
    benchmark_set = db.scalar(select(BenchmarkSet).where(BenchmarkSet.name == SUITE_NAME))
    if benchmark_set is not None and benchmark_set.dataset_sha256 != digest:
        raise ValueError("The frozen evaluation set hash changed; create a new suite version")
    if benchmark_set is None:
        benchmark_set = BenchmarkSet(
            name=SUITE_NAME,
            version=dataset["version"],
            description=(
                "Frozen deterministic CSV evaluation; expected outputs are fixture-checked. "
                "It does not measure provider quality or parallel performance."
            ),
            dataset_sha256=digest,
        )
        db.add(benchmark_set)
        db.flush()
    known = set(
        db.scalars(select(BenchmarkCase.case_key).where(BenchmarkCase.set_id == benchmark_set.id))
    )
    for item in dataset["cases"]:
        if item["case_id"] in known:
            continue
        db.add(
            BenchmarkCase(
                set_id=benchmark_set.id,
                case_key=item["case_id"],
                prompt=item["prompt"],
                input_data={
                    "csv": item["csv"],
                    "expected": item["expected"],
                    "category": item["category"],
                },
                expected_output_pattern="",
                routing_mode="fixed",
                is_active=True,
            )
        )
    db.flush()
    return benchmark_set


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and set(actual) == set(expected)
            and all(_matches(actual[key], value) for key, value in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_matches(a, e) for a, e in zip(actual, expected, strict=True))
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and math.isclose(
            actual, expected, rel_tol=1e-9, abs_tol=1e-9
        )
    return actual == expected


def execute_benchmark_case(
    case: BenchmarkCase,
    execution_id: str,
    workspace_id: str,
    mode: str,
    replicate_number: int,
    dataset_hash: str,
) -> BenchmarkResult:
    csv_text = case.input_data.get("csv", "")
    expected = case.input_data.get("expected", {})
    plan = demo_plan()
    outputs: dict[str, dict[str, Any]] = {}
    agents_used: list[str] = []
    total_tokens = 0
    failure_code: str | None = None
    workflow_id = str(uuid4())
    started = time.perf_counter()

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
        selected = REGISTRY.select(
            task.capability,
            agent_id=task.agent,
            schema_version=task.schema_version,
            agent_version=task.agent_version,
        )
        if mode != "single":
            selected, _ = select_route(
                mode=mode,
                requested_agent=task.agent,
                capability=task.capability,
                schema_version=task.schema_version,
                agent_version=task.agent_version,
                required_context=required_context,
                registry=REGISTRY,
                histories={},
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
        result = dispatch(request)
        if result.status != "succeeded" or result.structured_output is None:
            failure_code = result.error.code if result.error else "benchmark_agent_failed"
            break
        outputs[task.id] = result.structured_output
        agents_used.append(selected.agent_id)
        total_tokens += (result.usage.input_tokens or 0) + (result.usage.output_tokens or 0)

    elapsed_ms = max(1, round((time.perf_counter() - started) * 1000))
    final = outputs.get(plan.tasks[-1].id, {})
    expected_status = expected.get("status")
    actual_status = "failed" if failure_code else "succeeded"
    if expected_status == "succeeded":
        output_matches = not failure_code and _matches(
            final.get("summary"), expected.get("summary")
        )
    else:
        output_matches = bool(failure_code)
    score = 1.0 if output_matches else 0.0
    return BenchmarkResult(
        benchmark_case_id=case.id,
        workspace_id=workspace_id,
        execution_id=execution_id,
        routing_mode=mode,
        replicate_number=replicate_number,
        status="succeeded" if output_matches else "failed",
        duration_ms=elapsed_ms,
        tokens_used=total_tokens,
        # No provider call is made; provider cost is not estimated or fabricated.
        cost_usd=0.0,
        score=score,
        benchmark_metadata={
            "case_key": case.case_key,
            "category": case.input_data.get("category", "unknown"),
            "tasks_executed": len(agents_used),
            "agents_used": agents_used,
            "actual_status": actual_status,
            "expected_status": expected_status,
            "failure_code": failure_code,
            "expected_output_matched": output_matches,
            "execution_kind": "in_process_deterministic_dispatch",
            "dataset_sha256": dataset_hash,
            "replicate_number": replicate_number,
        },
    )


def execute_benchmark_suite(
    db: Session,
    workspace_id: str,
    set_id: str | None = None,
    routing_mode: str = "fixed",
    replicates: int = 3,
) -> list[BenchmarkResult]:
    if routing_mode not in ALLOWED_MODES:
        raise ValueError("Unsupported benchmark routing mode")
    benchmark_set = seed_evaluation_set(db)
    if set_id and set_id != benchmark_set.id:
        raise ValueError("Unknown evaluation set")
    dataset, digest = load_fixture()
    cases = list(
        db.scalars(
            select(BenchmarkCase)
            .where(BenchmarkCase.set_id == benchmark_set.id, BenchmarkCase.is_active.is_(True))
            .order_by(BenchmarkCase.case_key.asc())
        ).all()
    )
    if len(cases) != len(dataset["cases"]):
        raise ValueError("Stored evaluation cases do not match the frozen dataset")
    execution_id = str(uuid4())
    results = [
        execute_benchmark_case(case, execution_id, workspace_id, routing_mode, replicate, digest)
        for replicate in range(1, replicates + 1)
        for case in cases
    ]
    db.add_all(results)
    db.commit()
    for result in results:
        db.refresh(result)
    return results


def get_benchmark_summary(db: Session, workspace_id: str) -> dict[str, Any]:
    dataset, digest = load_fixture()
    benchmark_set = db.scalar(select(BenchmarkSet).where(BenchmarkSet.name == SUITE_NAME))
    results = list(
        db.scalars(
            select(BenchmarkResult)
            .join(BenchmarkCase, BenchmarkCase.id == BenchmarkResult.benchmark_case_id)
            .where(
                BenchmarkResult.workspace_id == workspace_id,
                BenchmarkCase.set_id == (benchmark_set.id if benchmark_set else ""),
            )
        ).all()
    )
    by_mode: dict[str, list[BenchmarkResult]] = {mode: [] for mode in ALLOWED_MODES}
    for result in results:
        if result.routing_mode in by_mode:
            by_mode[result.routing_mode].append(result)

    def stats(items: list[BenchmarkResult]) -> dict[str, Any]:
        n = len(items)
        successes = sum(item.status == "succeeded" for item in items)
        rate = successes / n if n else 0.0
        # Wilson 95% interval; describes case-replicate outcomes, not independent users.
        z = 1.96
        denom = 1 + z * z / n if n else 1
        center = (rate + z * z / (2 * n)) / denom if n else 0.0
        half = z * math.sqrt(rate * (1 - rate) / n + z * z / (4 * n * n)) / denom if n else 0.0
        return {
            "runs": n,
            "replicates": len({item.replicate_number for item in items}),
            "success_rate": round(rate, 4),
            "success_rate_ci95": [
                round(max(0.0, center - half), 4),
                round(min(1.0, center + half), 4),
            ]
            if n
            else None,
            "mean_duration_ms": round(sum(item.duration_ms for item in items) / n, 1) if n else 0.0,
            "mean_score": round(sum(item.score for item in items) / n, 4) if n else 0.0,
            "total_tokens": sum(item.tokens_used for item in items),
            "total_cost_usd": 0.0,
        }

    return {
        "suite_name": SUITE_NAME,
        "dataset_sha256": digest,
        "case_count": len(dataset["cases"]),
        "total_executions": len({result.execution_id for result in results}),
        "baselines": {mode: stats(by_mode[mode]) for mode in ALLOWED_MODES},
        "parallel_speedup": None,
        "evaluation_notes": (
            "Single is direct registry dispatch; fixed and adaptive use their routing policies. "
            "The local registry has one agent per capability, so fixed and adaptive currently "
            "select the same agents. These deterministic in-process replicates measure local "
            "sequential dispatch and fixture agreement, not provider quality, parallel speedup, "
            "or external API cost. Confidence intervals treat case-replicates as observations."
        ),
    }
