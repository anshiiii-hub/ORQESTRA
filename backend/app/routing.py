"""Deterministic, policy-filtered capability routing with auditable score factors."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from .registry import AgentDefinition, AgentRegistry

POLICY_VERSION = "routing-v1"
APPROVED_TOOLS: frozenset[str] = frozenset()
WEIGHTS = {
    "reliability": 0.45,
    "latency": 0.25,
    "availability": 0.20,
    "cost": 0.10,
}
LATENCY_TARGET_MS = 5000.0
COST_TARGET_USD = Decimal("0.01")
MAX_AVAILABILITY_WORKERS = 3


class RoutingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RoutingHistory:
    completed: int = 0
    succeeded: int = 0
    average_latency_ms: float = 0.0
    average_cost_usd: Decimal = Decimal(0)


def score_factors(history: RoutingHistory, available_workers: int) -> dict[str, float]:
    """Return deterministic, bounded factors with a small reliability prior."""
    completed = max(history.completed, 0)
    succeeded = min(max(history.succeeded, 0), completed)
    reliability = (succeeded + 1) / (completed + 2)
    latency = 1 / (1 + max(history.average_latency_ms, 0) / LATENCY_TARGET_MS)
    availability = min(max(available_workers, 0), MAX_AVAILABILITY_WORKERS) / float(
        MAX_AVAILABILITY_WORKERS
    )
    cost = float(1 / (1 + max(history.average_cost_usd, Decimal(0)) / COST_TARGET_USD))
    return {
        "reliability": round(reliability, 6),
        "latency": round(latency, 6),
        "availability": round(availability, 6),
        "cost": round(cost, 6),
    }


def weighted_score(factors: dict[str, float]) -> float:
    return round(sum(factors[name] * weight for name, weight in WEIGHTS.items()), 6)


def _ineligible_reason(
    agent: AgentDefinition,
    *,
    capability: str,
    schema_version: str,
    agent_version: str,
    required_context: Mapping[str, str],
    approved_tools: frozenset[str],
    mode: str,
    available_workers: int,
) -> str | None:
    if not agent.enabled:
        return "agent_disabled"
    if agent.capability != capability:
        return "capability_mismatch"
    if agent.schema_version != schema_version or agent.version != agent_version:
        return "incompatible_version"
    if dict(agent.required_context) != dict(required_context):
        return "input_contract_mismatch"
    if not set(agent.allowed_tools).issubset(approved_tools):
        return "tool_permission_denied"
    if mode == "adaptive" and available_workers < 1:
        return "no_compatible_worker"
    return None


def select_route(
    *,
    mode: str,
    requested_agent: str,
    capability: str,
    schema_version: str,
    agent_version: str,
    required_context: Mapping[str, str],
    registry: AgentRegistry,
    histories: dict[str, RoutingHistory] | None = None,
    worker_counts: dict[str, int] | None = None,
    approved_tools: frozenset[str] = APPROVED_TOOLS,
) -> tuple[AgentDefinition, dict]:
    """Choose a requested fixed route or the top adaptive candidate and explain why."""
    if mode not in ("fixed", "adaptive"):
        raise RoutingError("routing_mode_invalid", "Routing mode is not supported")
    histories = histories or {}
    worker_counts = worker_counts or {}
    evaluations = []
    eligible: list[tuple[AgentDefinition, dict[str, float], float]] = []
    for agent in registry.all():
        reason = _ineligible_reason(
            agent,
            capability=capability,
            schema_version=schema_version,
            agent_version=agent_version,
            required_context=required_context,
            approved_tools=approved_tools,
            mode=mode,
            available_workers=worker_counts.get(agent.agent_id, 0),
        )
        if reason:
            evaluations.append({"agent": agent.agent_id, "eligible": False, "reason": reason})
            continue
        history = histories.get(agent.agent_id, RoutingHistory())
        available_workers = worker_counts.get(agent.agent_id, 0)
        factors = score_factors(history, available_workers)
        score = weighted_score(factors)
        eligible.append((agent, factors, score))
        evaluations.append(
            {
                "agent": agent.agent_id,
                "eligible": True,
                "score": score,
                "factors": factors,
                "observations": {
                    "completed_attempts": history.completed,
                    "successful_attempts": history.succeeded,
                    "average_latency_ms": round(history.average_latency_ms, 3),
                    "average_reported_cost_usd": str(history.average_cost_usd),
                    "compatible_workers": available_workers,
                },
            }
        )

    if mode == "fixed":
        selected = next((item for item in eligible if item[0].agent_id == requested_agent), None)
    else:
        selected = (
            min(eligible, key=lambda item: (-item[2], item[0].agent_id)) if eligible else None
        )
    if selected is None:
        raise RoutingError("no_eligible_agent", "No registered agent satisfies the routing policy")

    agent, factors, score = selected
    explanation = {
        "policy_version": POLICY_VERSION,
        "mode": mode,
        "requested_agent": requested_agent,
        "selected_agent": agent.agent_id,
        "capability": capability,
        "score": score,
        "factors": factors,
        "weights": WEIGHTS,
        "eligible_candidate_count": len(eligible),
        "candidates": sorted(evaluations, key=lambda item: str(item["agent"])),
    }
    return agent, explanation
