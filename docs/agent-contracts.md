# Agent protocol and registry (Checkpoint 04)

The deterministic CSV roles now run through one validated dispatch interface. The registry contains three built-in agents; no arbitrary imports, shell commands, network tools, or model calls are supported.

| Agent ID | Capability | Required context aliases | Output |
| --- | --- | --- | --- |
| csv_inspector | csv.inspect | none | Row count, schema, parsed rows |
| csv_summarizer | csv.summarize | inspect | Numeric counts, totals, means, ranges |
| result_verifier | result.verify | inspect, summarize | Explicit pass, checks and source-verified summary |

All agent implementations are version `1.0.0`; their wire protocol is `1.0`. The role is a Python function, not a separate model or remote service.

## Message contracts

`backend/contracts/` contains committed JSON Schema documents for requests, results, and all three structured outputs. Runtime Pydantic models in `backend/app/contracts.py` use strict types, reject unknown fields, and enforce additional cross-field invariants. JSON Schema alone does not express every runtime invariant.

A request explicitly identifies `schema_version`, `request_id`, `workflow_id`, `task_id`, `agent_type`, `agent_version`, `capability`, and `objective`. Its CSV payload holds only the required parent outputs. `context_refs` binds each alias to a persisted parent task ID. Artifact references are identifiers, never host paths; nonempty input artifact lists are rejected until an artifact service exists.

A result repeats the request's identity and version fields. It includes status, summary, structured output, source references, usage, warnings, and a typed error. Success requires validated output and no error. Failure requires an error and cannot publish output or artifacts. IDs, versions, schemas, finite numbers, row/column consistency, and source references are checked before success can be saved. The verifier separately recalculates arithmetic from the source CSV.

Failures carry a code, stage, retryable flag, and bounded safe message. Raw exception messages and invalid outputs are not copied into result envelopes or events. Current deterministic agents have zero model token/cost usage. No automatic retries are added in this checkpoint. Task rows retain the latest request/result envelopes; separate per-attempt history is planned for the scheduler checkpoint. Started/succeeded events retain their request IDs.

## Planning and dispatch

Plans name required capabilities and explicit input bindings, independently of task IDs. Each binding must reference a declared dependency with the correct capability. Cycle, missing/duplicate dependency, duplicate ID, unknown agent, disabled agent, and incompatible version checks reject an invalid plan. If the configured demo agents are unavailable, workflow creation returns 503 without inserting a workflow.

The worker validates persisted task metadata again at dispatch. It validates required parent outputs, saves a request envelope, executes outside database locks, validates the returned output, and saves the result envelope alongside the existing result format. Invalid results fail the workflow; pending tasks are skipped and cannot consume invalid output. Cancellation observed after computation discards its output.

Task detail responses include capability, implementation/protocol versions, error code, and the result envelope. Request envelopes contain source CSV/context and remain internal database records; they are not returned by the workflow API. Existing workspace authorization also protects result envelopes.

Version compatibility is explicit: untagged or unsupported protocol/implementation versions are rejected. New incompatible formats need new versioned models and schema files. Do not silently coerce a future message into version 1.0.

## Permissions and limits

The dispatcher permits requested tools only when they are present in both the registered agent allowlist and the server-supplied effective permissions. All current allowlists are empty. A prompt, payload, or agent response cannot grant itself permissions. No tool execution gateway is introduced yet.

CSV input keeps its existing character/row/column limits. Serialized successful result envelopes are capped at 1,000,000 bytes by default. Execution deadlines are checked after the bounded pure CSV function returns; in-process code is not forcibly interrupted. Hard preemption and sandbox resource limits belong to later scheduler/sandbox checkpoints. Token budgets are reserved for later LLM adapters; these deterministic functions use no model tokens.

## Registry API and heartbeats

Authenticated `GET /api/v1/agents` returns capability/version/schema metadata, allowed tools, configured enabled state, compatible live-worker count, and availability. Authenticated `GET /api/v1/agents/contracts` returns the runtime JSON Schemas. This global catalog contains no workspace task inputs or results. Registry mutation is not exposed to workspace owners.

Each worker process records a heartbeat every five seconds. A matching agent/protocol advertisement seen within 30 seconds is `ready`; otherwise it is `offline`. A configured disabled agent is `disabled`. Heartbeats are observational liveness only: they do not reserve work or guarantee capacity. Task leases, rather than heartbeat rows, coordinate multiple workers. Expired heartbeat rows are ignored and are not automatically pruned.

An operator can set `DISABLED_AGENTS=csv_inspector,csv_summarizer` in `.env` and restart/recreate the API and worker. Compose passes the same configuration to both. Unknown IDs fail startup instead of being silently ignored. Existing queued work requiring a disabled agent fails safely.

## Migration and checks

Run `alembic upgrade head` (Compose's migration service does this before API/worker startup). Revision `0003` adds task versions, bindings, request/result envelopes, error codes, and a heartbeat table. It backfills known Checkpoint 01–03 CSV tasks and makes the verifier's inspection dependency explicit. Completed legacy results remain intact and have no fabricated protocol envelope. Unrecognized custom legacy tasks are not guessed into a supported capability.

From `backend/`:

```sh
python -m app.export_contracts
python -m app.export_contracts --check
pytest -q
```

The schema drift check runs in CI. SQLite and disposable PostgreSQL integration tests exercise queue migration/resumption, contract persistence, capability/version failures, cancellation, malformed parent/agent outputs, and stale/incompatible/disabled worker advertisements.
