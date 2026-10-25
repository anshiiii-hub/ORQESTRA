# ORQESTRA AI — Checkpoint 01 (runnable prototype)
> **Design and Development of an Adaptive, Fault-Tolerant Multi-Agent LLM Orchestration Framework**

This is the **first engineering checkpoint**, not the complete 7-agent product. It runs a deterministic three-agent CSV workflow through a persisted PostgreSQL task graph. No paid AI account is necessary.

## What actually works
- React/TypeScript dashboard, workflow submission, task graph, metrics, event log and verified output.
- FastAPI routes for create/list/read/cancel/events and an optional authenticated SSE endpoint.
- Pydantic DAG validation with cycle, duplicate-ID and missing-dependency rejection.
- Three deterministic agents: CSV inspection, numeric summarization, result verification.
- PostgreSQL workflow/task/event persistence; a separate polling worker.
- Automatic requeue of interrupted pure tasks when the single worker restarts.
- A simple development API key; bound to loopback only in Compose.
- A Redis service provisioned for a later checkpoint (not in the current execution path).

## Setup
1. Install Docker Desktop (Compose v2).
2. Copy `.env.example` to `.env`. Replace both secrets. For local experimentation only, API_KEY may be any long random string.
3. If you already have Checkpoint 01 data, follow [database adoption](docs/database-migrations.md#existing-checkpoint-01-database) first. For a fresh database, run `docker compose up --build -d` in the root folder; the migration service prepares the schema before the API and worker start.
4. Run `docker compose ps` and check that `postgres`, `redis`, `api`, `worker`, and `frontend` are running.
5. Open `http://localhost:5173` and paste your `.env` `API_KEY` into the development API key field.
6. Use the prefilled CSV and click **Execute workflow**. Inspect the task graph, event log, and verified output.
7. Run backend tests: `docker compose exec api pytest -q`.
8. Shut down with `docker compose down`. To remove **all local database data**, run `docker compose down -v` (irreversible).

### API example
```bash
curl -X POST http://localhost:8001/api/v1/workflows \
  -H 'Content-Type: application/json' -H 'X-API-Key: YOUR_API_KEY' \
  -d '{"prompt":"Analyze and verify these sales","csv_text":"item,sales\nA,10\nB,20"}'
```
The response is HTTP 202 with a workflow ID. Use GET `/api/v1/workflows/{id}` with the same header to read progress. Interactive API documentation: `http://localhost:8001/docs` by default (override with `API_PORT`).

## Security and deployment boundaries
- Validation status (25 October 2026): all 25 backend tests passed locally and in an isolated API container; the frontend production build passed. All five Compose services were running, with PostgreSQL and Redis healthy. An isolated SQLite workflow smoke test passed. Browser-to-PostgreSQL execution still needs an end-to-end acceptance check.

- **Do not expose this prototype publicly or deploy it to production.** The single shared API key is development-only; there is no per-user RBAC, workspace isolation, login, CSRF policy, token rotation or rate limiting.
- Frontend stores the development API key in browser localStorage, suitable only for a personal local demo; replace with server-backed sessions before shared deployment.
- The UI polls the REST endpoint every ~1.8 seconds. Optional `/stream` SSE endpoint requires a request header that native browser EventSource cannot supply; a production client needs a session cookie or a controlled SSE fetch client.
- Alembic manages the schema. API and worker startup require the current migration revision. Existing unversioned databases require explicit, schema-checked adoption; see [migration instructions](docs/database-migrations.md).
- One worker only. Multi-worker task leases and work claiming are deferred. The current worker holds a transaction lock only for scheduling, and task execution happens outside it; concurrent workers are not tested.
- Crash recovery applies to **pure, repeatable CSV operations** only; external write tools require stronger deduplication and human approval.
- CSV rows are limited to 1000, 40 columns, and 250,000 input characters. Parsing is memory-based, not a hardened upload service.
- All workflows share the demonstration database. No isolation or retention implementation yet.
- Python and Node dependencies should be updated and vulnerability-scanned before deployment. No unrestricted shell or network tool is exposed to agents.

## Roadmap to Checkpoint 02
1. Proper identity/workspaces/RBAC, migrations, object storage.
2. Versioned agent request/result contracts; LLM adapter with structured planner output and strict validation.
3. Redis-backed worker queue, multi-worker leases and heartbeats, durable recovery.
4. Capability-aware routing, policy-enforced tool gateway, controlled retries and limits.
5. Parallel ready-node execution, verifiers, checkpoints and benchmark harness.

## Repository
```
backend/app/  FastAPI API, models, planner, agents and worker
backend/tests/  DAG and CSV chain tests
frontend/src/  React dashboard
compose.yaml    Local services
.env.example    Configuration template
```
