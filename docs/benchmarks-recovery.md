# Recovery controls and benchmark smoke suite

This document describes the recovery controls and deterministic smoke suite currently in the repository. They are useful implementation slices, but they do not by themselves satisfy every exit gate in the project handbook.

## Recovery behavior

- Workflows can be paused from `queued` or `running`, resumed from `paused`, and retried only from `failed` or `cancelled`.
- Succeeded task outputs and attempt records are preserved by retry. Failed, skipped, cancelled, and approval-waiting tasks are reset when checkpoint preservation is enabled.
- A worker that finishes after a pause does not publish its result. Its attempt is superseded and its task is returned to `pending`, so resume can safely execute it again.
- Resume keeps unexpired task leases intact. If a worker died while paused, normal lease expiry recovery requeues the task after the workflow resumes.
- `GET /api/v1/workflows/{id}/checkpoint` reports task states and available transitions. Workflow recovery routes enforce workspace membership and role checks.

This stores recovery state in the existing workflow/task tables. It is not a LangGraph PostgreSQL checkpointer, and a live worker-kill/restart acceptance test still needs a PostgreSQL environment.

## Benchmark smoke suite

`POST /api/v1/benchmarks/executions` runs the registered deterministic CSV agent DAG in-process for four fixed/adaptive fixtures. It exercises route selection, contract validation, dispatch, and output checks. Results record elapsed local dispatch time, actual agent IDs, task count, result score, and the usage reported by agents.

The built-in registry has one compatible agent for each capability, so fixed and adaptive modes select the same agents today. The suite is sequential, uses only a few fixtures, and makes no provider calls. It does not measure parallel speedup, production latency, comparative routing quality, or meaningful statistical confidence. Those fields must not be inferred from this smoke suite. `GET /api/v1/benchmarks/summary` reads recorded results without creating benchmark data.

## Health and workspace metrics

- `GET /healthz` checks that the API process responds.
- `GET /readyz` checks database connectivity and that the current Alembic heads match the repository migration heads. It reports recent worker heartbeats for diagnostics; worker count is not a readiness gate.
- `GET /api/v1/metrics/overview?workspace_id={id}` returns workspace-scoped workflow counts and recovery metrics. Recovery rate is `null` until at least one workflow has been paused; it counts workflows with a resume record divided by workflows with a recorded pause history.

## Handbook checkpoint alignment

The handbook numbers recovery/approvals as Checkpoint 13, dashboard/observability as 14, benchmarks/security review as 15, and cloud deployment as 16. Repository feature commits use a different sequence. Checkpoint 15 evaluation and security-review scope, methods, findings, and limits are recorded in [the Checkpoint 15 review](checkpoint15-security-review.md). Cloud deployment and final-submission evidence are still outstanding.
