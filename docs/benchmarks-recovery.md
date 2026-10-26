# Durable checkpoint recovery, benchmarks, and deployment resilience

Checkpoint 14 implements durable checkpoint state persistence, workflow pause/resume/retry state transitions (preserving completed task boundaries), standardized benchmark evaluation suites, baseline comparison metrics (Fixed vs. Adaptive orchestration), deep operational readiness checks, and aggregated workspace telemetry (PRD Sections 8.4, 9.3, 14, 15, 19, US-09, FR-D05).

## Architectural policy and security boundary

1. **Durable checkpoint boundary preservation (PRD Section 8.4, Demo D)**:
   - Workflows execute across a deterministic task graph persisted in PostgreSQL.
   - When a workflow is paused (`POST /api/v1/workflows/{id}/pause`), the engine freezes execution without cancelling or mutating already completed tasks:
     - Tasks in `succeeded` status are immutable and preserved with their output envelopes and attempts.
     - Active tasks with unexpired leases can complete or are reset to `pending` upon worker restart.
     - Workers polling the queue ignore workflows in `paused` status (`Workflow.status.in_(["queued", "running"])`).
   - When resumed (`POST /api/v1/workflows/{id}/resume`):
     - Workflow status transitions back to `queued`.
     - The scheduler claims and dispatches only remaining `pending` or `ready` tasks whose dependencies are satisfied.
     - Completed tasks are **never re-run**, guaranteeing idempotent boundary recovery.
   - When retried (`POST /api/v1/workflows/{id}/retry`):
     - If `preserve_checkpoint: true`, succeeded nodes stay intact, while failed, cancelled, or skipped tasks are reset to `pending`.
     - If `preserve_checkpoint: false`, full replay is executed.

2. **Standard benchmark evaluation suite (PRD Section 10, 15, US-09, FR-D05)**:
   - Curated standardized benchmark suite (`Standard Orchestration Suite v1`):
     - `csv_statistics_fixed`: Single-agent fixed baseline for numerical computation.
     - `csv_statistics_adaptive`: Adaptive orchestrator scoring reliability, latency, and cost.
     - `numeric_verification_fixed`: Arithmetic validation and data integrity verification.
     - `resilience_checkpoint_recovery`: Fault tolerance and mid-flight checkpoint recovery test.
   - Automated evaluation matrix:
     - **Task success rate**: $\text{SuccessRate} = \frac{\text{Criteria Met}}{\text{Attempted Tasks}}$.
     - **Parallel speedup**: $\text{Speedup} = \frac{\text{Mean Latency}_{\text{fixed}}}{\text{Mean Latency}_{\text{adaptive}}}$.
     - **Recovery rate**: $\text{RecoveryRate} = \frac{\text{Resumed Interrupted Runs}}{\text{Eligible Interrupted Runs}}$.
     - **Cost accounting**: Accumulated tokens and billed cost per successful task run.

3. **Operational readiness and liveness probes (PRD Section 19)**:
   - `/healthz`: Lightweight liveness probe confirming the FastAPI process is alive and responsive.
   - `/readyz`: Deep readiness probe verifying:
     - Active PostgreSQL database connection (`SELECT 1`).
     - Alembic database migration schema is applied to head revision (`0013`).
     - Active worker processes have published a recent heartbeat within 60 seconds (`WorkerHeartbeat.seen_at`).
   - `/api/v1/metrics/overview`: Workspace-scoped telemetry endpoint returning workflow status distributions, active workers, and recovery fractions.

4. **Multi-tenant isolation and role-based access control**:
   - Workflows, tasks, and checkpoint metadata are strictly isolated to their owning workspace.
   - Members and owners can pause, resume, retry, or cancel workflows.
   - Viewers can inspect `/checkpoint` status and `/metrics/overview`, but cannot trigger state changes (HTTP 403 Forbidden).
   - Cross-tenant requests to workflows in other workspaces return HTTP 404.

---

## State machine transitions

```
                +-------------------------------------------------+
                |                  Workflow Created               |
                +-------------------------------------------------+
                                         |
                                         v
                         +-------------------------------+
                         |            QUEUED             |
                         +-------------------------------+
                                         |
                                         v
               +---------------------------------------------------+
               |                    RUNNING                        |
               | (Worker claims and executes tasks checkpointed)   |
               +---------------------------------------------------+
                   /                     |                     \
    [User Pauses] /       [Task Failure] |                      \ [All Tasks Done]
                 v                       v                       v
     +-----------------------+  +-------------------+  +-------------------+
     |        PAUSED         |  |      FAILED       |  |     SUCCEEDED     |
     | (Checkpoints frozen)  |  | (Retry available) |  | (Final output)    |
     +-----------------------+  +-------------------+  +-------------------+
                 |                       |
   [User Resumes]|                       | [User Retries (preserve_checkpoint)]
                 v                       v
     +-----------------------------------------------+
     |              QUEUED (Resumed)                 |
     | (Succeeded tasks preserved; pending continue) |
     +-----------------------------------------------+
```

---

## REST API reference

### Workflow pause and resume
`POST /api/v1/workflows/{id}/pause`
- Suspends active execution and saves checkpoint metadata.

`POST /api/v1/workflows/{id}/resume`
- Resumes execution from checkpoint boundary without re-running pure completed tasks.

`POST /api/v1/workflows/{id}/retry`
- Body: `{"preserve_checkpoint": true}`. Resets failed tasks while preserving completed ones.

`GET /api/v1/workflows/{id}/checkpoint`
- Returns checkpoint progress, succeeded tasks, pending tasks, and recovery capabilities.

### Benchmark suite execution
`POST /api/v1/benchmarks/executions`
- Body: `{"set_id": null, "routing_mode": "adaptive"}`. Launches benchmark run across standard cases.

`GET /api/v1/benchmarks/summary`
- Returns aggregated scientific evaluation matrix comparing Fixed vs Adaptive baselines, recovery rate, and speedup ratio.

`GET /api/v1/benchmarks/executions/{id}`
- Returns individual task results for a specific execution run.

### Deep operational health checks
`GET /healthz`
- Process liveness check.

`GET /readyz`
- Deep readiness check verifying DB connection, migration head, and active workers.

`GET /api/v1/metrics/overview?workspace_id={id}`
- Workspace-scoped telemetry overview.
