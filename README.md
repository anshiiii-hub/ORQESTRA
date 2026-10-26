# ORQESTRA AI — Checkpoint 06 (leased DAG scheduler)
> **Design and Development of an Adaptive, Fault-Tolerant Multi-Agent LLM Orchestration Framework**

This prototype runs a deterministic CSV workflow through a persisted PostgreSQL task graph. Checkpoint 05 added optional OpenAI planning; Checkpoint 06 adds normalized dependency edges, durable execution attempts, expiring worker leases, fenced result publication, concurrent ready-node execution, and prompt cancellation. Offline planning remains the default and makes no provider calls. The full multi-agent LLM product remains planned. No paid AI account is necessary.

## What actually works
- React/TypeScript dashboard, workflow submission, task graph, metrics, event log and verified output.
- FastAPI routes for create/list/read/cancel/events and an optional authenticated SSE endpoint.
- Pydantic DAG validation with cycle, duplicate-ID and missing-dependency rejection.
- Three deterministic agents: CSV inspection, numeric summarization, result verification.
- Optional OpenAI planning with explicit opt-in, server-held credentials, token/cost/deadline checks, and a two-call maximum; see [LLM planning configuration](docs/llm-planning.md).
- Persisted planning attempts and accepted plans, conservative recovery of uncertain provider charges, and a 20-case replay corpus.
- Normalized task dependency edges, durable per-run attempt records, idempotency keys, expiring worker leases, and stale-result fencing.
- Multiple worker replicas can claim independent ready tasks concurrently; expired leases are recovered without interrupting another live worker.
- Scheduler lease and cancellation behavior, concurrency verification, and scale-out instructions are documented in [the scheduler guide](docs/scheduler.md).
- Explicit protocol 1.0 / implementation 1.0.0 contracts, JSON Schemas, typed input/output validation, and sanitized failure codes.
- Capability and input-binding checks before dispatch; invalid results stop the workflow and skip pending tasks.
- Agent registry, compatible-worker availability, and execution request IDs in the dashboard; see [agent contracts](docs/agent-contracts.md).
- PostgreSQL workflow/task/event persistence; a separate polling worker.
- Lease-based recovery of interrupted pure tasks after worker failure or lease expiry.
- Account registration/login, HttpOnly cookie sessions, CSRF protection, and workspace-scoped permissions; bound to loopback in Compose.
- Workspace creation/selection and owner-only member management in the dashboard.
- A Redis service provisioned for a later checkpoint (not in the current execution path).
- Hashed Python dependency locks and a frontend lockfile used by Docker builds.
- Alembic baseline migration, schema-checked database adoption, and startup revision checks.
- Ruff, Mypy, pre-commit hooks, and GitHub Actions for backend and frontend checks; see [development instructions](docs/development.md).

## Setup
1. Install Docker Desktop (Compose v2).
2. Copy `.env.example` to `.env`. Replace `POSTGRES_PASSWORD` with a strong development secret. Keep `SESSION_COOKIE_SECURE=false` for local HTTP and set `APP_ORIGIN` to the exact frontend URL.
3. If you already have Checkpoint 01 data, follow [database adoption](docs/database-migrations.md#existing-checkpoint-01-database) first. For a fresh database, run `docker compose up --build -d` in the root folder; the migration service prepares the schema before the API and worker start.
4. Run `docker compose ps` and check that `postgres`, `redis`, `api`, `worker`, and `frontend` are running.
5. Open `http://localhost:5173`, create an account, and create a workspace. Existing users can sign in. Owners can add other registered users as members or viewers.
6. Keep **Offline pipeline** selected, use the prefilled CSV, and click **Execute workflow**. Optional OpenAI planning requires [server configuration](docs/llm-planning.md#optional-operator-configuration) and an explicit **Plan and execute** action. Inspect the graph, planning ledger, validated plan, and verified output.
7. Run backend tests: `docker compose exec api pytest -q`.
8. Shut down with `docker compose down`. To remove **all local database data**, run `docker compose down -v` (irreversible).

### API access

The API uses a session cookie and an in-memory CSRF token. Shared API keys no longer grant access. Follow the [authentication API sequence](docs/authentication.md#api-sequence) for login, workspace creation, workflow submission, and event replay. Interactive API documentation: `http://localhost:8001/docs` by default (override with `API_PORT`).

## Security and deployment boundaries
- Validation status (26 October 2026): 181 local tests pass and two PostgreSQL-only lease/concurrency tests are skipped locally and included in CI. Ruff, Mypy, schema drift checks, and the frontend production build pass. Checkpoint 05 passed PostgreSQL planning/runtime checks and GitHub Actions. Live-model quality remains unmeasured; no paid API calls were made.

- **Do not expose this prototype publicly or deploy it to production.** Email verification, password reset, login rate limiting, and production TLS hardening remain future work. See [authentication scope and limitations](docs/authentication.md#scope-and-remaining-hardening).
- Passwords use salted scrypt. Session cookies are HttpOnly and SameSite=Lax; authenticated writes require a session-bound CSRF token and trusted origin. Enable secure cookies for HTTPS.
- The UI polls REST every ~1.8 seconds. Optional SSE uses the session cookie, supports event cursors, and rechecks access during streaming.
- Alembic manages the schema. API and worker startup require the current migration revision. Existing unversioned databases require explicit, schema-checked adoption; see [migration instructions](docs/database-migrations.md).
- Workers claim workflow rows briefly with `FOR UPDATE SKIP LOCKED`, persist a task attempt and lease before dispatch, and execute outside database locks. Task results publish only while the current attempt still owns the lease. The lease lasts 360 seconds (longer than the 300-second contract timeout). Execution uses at-least-once recovery: a task may run again after its lease expires, so agents must remain idempotent and free of external side effects.
- Pure CSV tasks may requeue after restart. A potentially billable planning request is not replayed when its outcome is uncertain; its reservation is retained and the workflow fails explicitly. External write tools still require stronger deduplication and approval.
- LLM planning sends task instructions, CSV column names and row count to the configured provider; CSV row values stay local. Planning costs are estimates from operator-maintained price snapshots, not an invoice cap.
- CSV rows are limited to 1000, 40 columns, and 250,000 input characters. Parsing is memory-based, not a hardened upload service.
- Workflow reads, outputs, cancellations, and events are workspace-scoped. Legacy workflows remain hidden until explicitly assigned by a local operator. Retention and document/artifact authorization will be added with those features.
- Python and Node dependencies should be updated and vulnerability-scanned before deployment. No unrestricted shell or network tool is exposed to agents.

## Next checkpoints
1. Checkpoints 07–08: adaptive routing and restricted research tools.
3. Later checkpoints: expanded agents, reporting, durable recovery, benchmarks, and deployment.

## Repository
```
backend/app/  FastAPI API, models, planner, agents and worker
backend/tests/  DAG, CSV, migrations, auth, protocol and runtime tests
backend/contracts/  Versioned JSON Schema exports
frontend/src/  React dashboard
compose.yaml    Local services
.env.example    Configuration template
```
