# ORQESTRA AI — Checkpoint 02 (repository hardening)
> **Design and Development of an Adaptive, Fault-Tolerant Multi-Agent LLM Orchestration Framework**

This prototype runs a deterministic three-agent CSV workflow through a persisted PostgreSQL task graph. Checkpoint 02 adds dependency locks, Alembic migrations, development checks, and CI. The full multi-agent LLM product remains planned. No paid AI account is necessary.

## What actually works
- React/TypeScript dashboard, workflow submission, task graph, metrics, event log and verified output.
- FastAPI routes for create/list/read/cancel/events and an optional authenticated SSE endpoint.
- Pydantic DAG validation with cycle, duplicate-ID and missing-dependency rejection.
- Three deterministic agents: CSV inspection, numeric summarization, result verification.
- PostgreSQL workflow/task/event persistence; a separate polling worker.
- Automatic requeue of interrupted pure tasks when the single worker restarts.
- A simple development API key; bound to loopback only in Compose.
- A Redis service provisioned for a later checkpoint (not in the current execution path).
- Hashed Python dependency locks and a frontend lockfile used by Docker builds.
- Alembic baseline migration, schema-checked database adoption, and startup revision checks.
- Ruff, Mypy, pre-commit hooks, and GitHub Actions for backend and frontend checks; see [development instructions](docs/development.md).

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
- Validation status (25 October 2026): 29 backend tests passed in an isolated Python 3.12 container, including migration, adoption, and authenticated API-to-worker tests. Migration/API checks also passed against a disposable PostgreSQL 16 database. Ruff lint/format, Mypy, hashed dependency installation, API image build, and frontend production build passed. Browser-based acceptance remains a separate check.

- **Do not expose this prototype publicly or deploy it to production.** The single shared API key is development-only; there is no per-user RBAC, workspace isolation, login, CSRF policy, token rotation or rate limiting.
- Frontend stores the development API key in browser localStorage, suitable only for a personal local demo; replace with server-backed sessions before shared deployment.
- The UI polls the REST endpoint every ~1.8 seconds. Optional `/stream` SSE endpoint requires a request header that native browser EventSource cannot supply; a production client needs a session cookie or a controlled SSE fetch client.
- Alembic manages the schema. API and worker startup require the current migration revision. Existing unversioned databases require explicit, schema-checked adoption; see [migration instructions](docs/database-migrations.md).
- One worker only. Multi-worker task leases and work claiming are deferred. The current worker holds a transaction lock only for scheduling, and task execution happens outside it; concurrent workers are not tested.
- Crash recovery applies to **pure, repeatable CSV operations** only; external write tools require stronger deduplication and human approval.
- CSV rows are limited to 1000, 40 columns, and 250,000 input characters. Parsing is memory-based, not a hardened upload service.
- All workflows share the demonstration database. No isolation or retention implementation yet.
- Python and Node dependencies should be updated and vulnerability-scanned before deployment. No unrestricted shell or network tool is exposed to agents.

## Next checkpoints
1. Checkpoint 03: identity, workspaces, memberships, RBAC, and session security.
2. Checkpoint 04: versioned agent request/result contracts and capability registry.
3. Checkpoint 05: LLM adapter with structured planner output and strict validation.
4. Checkpoints 06–08: concurrent scheduling, leases, routing, and restricted tools.
5. Later checkpoints: expanded agents, reporting, durable recovery, benchmarks, and deployment.

## Repository
```
backend/app/  FastAPI API, models, planner, agents and worker
backend/tests/  DAG and CSV chain tests
frontend/src/  React dashboard
compose.yaml    Local services
.env.example    Configuration template
```
