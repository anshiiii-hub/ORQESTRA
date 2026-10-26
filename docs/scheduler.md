# Leased DAG scheduler (Checkpoint 06)

## Claim and dispatch flow

1. Workers select the oldest queued or running workflow with `FOR UPDATE SKIP LOCKED` and hold that row lock only while scheduling.
2. The worker compares the task's JSON dependency list with normalized rows in `task_dependencies`. A mismatch fails safely before agent dispatch.
3. A pending node becomes claimable only after every dependency edge points to a succeeded task. Independent ready nodes can be claimed by different workers.
4. The worker inserts a `task_attempts` record, a per-attempt idempotency key, and a lease in the same database transaction that marks the task running and persists its request envelope.
5. Agent execution happens after commit, outside database locks. The result is committed only if the attempt still matches the task's current attempt and lease owner.

## Leases and recovery

The lease lasts 360 seconds, above the agent protocol's maximum declared timeout of 300 seconds. Workers do not clear a lease owned by another worker during startup. A worker may reclaim an expired task; it marks the earlier attempt `lease_expired`, records an event, and issues a new attempt number and idempotency key. If an old worker returns after a newer attempt has claimed the task, its result is discarded by the attempt ID fence.

This is at-least-once execution. A process can stop after performing pure work and before committing the result, so a later worker may repeat that work. Current agents are deterministic CSV functions with no external side effects. Any future external write tool needs its own idempotency mechanism and approval boundary.

## Cancellation

Cancellation marks pending and running tasks cancelled, cancels their active attempt records, and clears task leases in one API transaction. It prevents new claims and result publication. Python handlers are not forcibly terminated; a handler already executing may finish in the background, but its result cannot be published after cancellation.

## Operations and verification

Run multiple replicas with `docker compose up --scale worker=3 -d worker`. All replicas use PostgreSQL as the durable queue; Redis is not required for correctness. The PostgreSQL integration test blocks two independent agent calls at a barrier and verifies they overlap, then checks both completed attempts. SQLite tests cover migration, task recovery, cancellation, and attempt persistence. Run the PostgreSQL test with the same disposable `TEST_DATABASE_URL` used by CI.
