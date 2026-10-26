# Capability-based routing (Checkpoint 07)

## Baselines

Every workflow stores a routing mode:

- `fixed` keeps the agent requested by the validated plan.
- `adaptive` selects the highest scoring agent from the policy-eligible set, with agent ID as a stable tie-break.

The UI lets the user select either baseline. Existing workflows and requests default to `fixed`. For a fair comparison, submit the same input once with each mode and compare the stored decisions, task outcomes, durations, and reported usage in the workflow records.

## Eligibility policy

An agent is eligible only when it is enabled, implements the exact requested capability and protocol versions, accepts the task's declared input contract, and requires no tool permissions outside the routing policy. Adaptive routing also requires at least one live worker heartbeat advertising the exact agent and protocol version. The approved tool set is empty in this checkpoint, so agents requiring tools cannot be selected.

The planner cannot grant capabilities or tool access. A proposed agent name is a routing hint; the validated task contract and local registry remain authoritative. The selected agent is written back into the accepted execution plan before task insertion.

## Score and explanation

The deterministic policy uses task-attempt history from the workflow's own workspace and the following weighted factors:

| Factor | Weight | Measurement |
| --- | ---: | --- |
| Reliability | 0.45 | Smoothed success share from the most recent 1,000 completed attempts in the registry, grouped by agent, with a one-success/one-failure prior |
| Latency | 0.25 | `1 / (1 + average_ms / 5000)` over completed attempts |
| Availability | 0.20 | Live compatible worker count, capped at three, divided by three |
| Cost | 0.10 | `1 / (1 + average_reported_cost / $0.01)` from result usage |

Every task stores the policy version, mode, requested and selected agent, candidate eligibility reasons, scores, component factors, and weights. A `route_selected` event records the same decision for event replay. The API and dashboard expose this explanation to workspace members.

The built-in registry currently has one agent for each capability, and all three are deterministic with zero reported execution cost. Therefore adaptive and fixed currently choose the same agents in normal runs. The scoring and policy tests use competing fixture agents to exercise eligibility, score ordering, and deterministic ties. Score weights are initial hypotheses; they need measured benchmarks before production use.
