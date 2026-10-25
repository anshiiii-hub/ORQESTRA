# ORQESTRA AI — Checkpoint 04 (agent protocol and registry)
> **Design and Development of an Adaptive, Fault-Tolerant Multi-Agent LLM Orchestration Framework**

This prototype runs a deterministic three-agent CSV workflow through a persisted PostgreSQL task graph. Checkpoint 04 adds strict versioned agent messages, capability-aware plan validation, persisted execution envelopes, and a registry with worker heartbeats. Accounts, workspace roles, migrations, and CI remain in place. The full multi-agent LLM product remains planned. No paid AI account is necessary.

## What actually works
- React/TypeScript dashboard, workflow submission, task graph, metrics, event log and verified output.
- FastAPI routes for create/list/read/cancel/events and an optional authenticated SSE endpoint.
- Pydantic DAG validation with cycle, duplicate-ID and missing-dependency rejection.
- Three deterministic agents: CSV inspection, numeric summarization, result verification.
- Explicit protocol 1.0 / implementation 1.0.0 contracts, JSON Schemas, typed input/output validation, and sanitized failure codes.
- Capability and input-binding checks before dispatch; invalid results stop the workflow and skip pending tasks.
- Agent registry, compatible-worker availability, and execution request IDs in the dashboard; see [agent contracts](docs/agent-contracts.md).
- PostgreSQL workflow/task/event persistence; a separate polling worker.
- Automatic requeue of interrupted pure tasks when the single worker restarts.
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
6. Check the agent registry, use the prefilled CSV, and click **Execute workflow**. Inspect the task graph, event log, verified output, and expandable execution contracts.
7. Run backend tests: `docker compose exec api pytest -q`.
8. Shut down with `docker compose down`. To remove **all local database data**, run `docker compose down -v` (irreversible).

### API access

The API uses a session cookie and an in-memory CSRF token. Shared API keys no longer grant access. Follow the [authentication API sequence](docs/authentication.md#api-sequence) for login, workspace creation, workflow submission, and event replay. Interactive API documentation: `http://localhost:8001/docs` by default (override with `API_PORT`).

## Security and deployment boundaries
- Validation status (26 October 2026): all 109 backend tests passed, including migration/resumption of queued tasks, strict contract rejection, capability/version checks, failure propagation, cancellation, heartbeat expiry, and access-control tests. Checks passed with SQLite and disposable PostgreSQL 16, and from the packaged API image. Ruff, Mypy, schema drift checks, and the frontend production build passed. Browser checks confirmed a successful contract-enabled CSV workflow, correlated request IDs, a compatible-worker registry, and offline/ready updates after stopping/restarting the disposable worker.

- **Do not expose this prototype publicly or deploy it to production.** Email verification, password reset, login rate limiting, and production TLS hardening remain future work. See [authentication scope and limitations](docs/authentication.md#scope-and-remaining-hardening).
- Passwords use salted scrypt. Session cookies are HttpOnly and SameSite=Lax; authenticated writes require a session-bound CSRF token and trusted origin. Enable secure cookies for HTTPS.
- The UI polls REST every ~1.8 seconds. Optional SSE uses the session cookie, supports event cursors, and rechecks access during streaming.
- Alembic manages the schema. API and worker startup require the current migration revision. Existing unversioned databases require explicit, schema-checked adoption; see [migration instructions](docs/database-migrations.md).
- One worker only. Heartbeats report liveness; they do not lease tasks or enable safe multi-worker execution. Multi-worker leases and work claiming are deferred. The current worker holds a transaction lock only for scheduling, and task execution happens outside it; concurrent workers are not tested.
- Crash recovery applies to **pure, repeatable CSV operations** only; external write tools require stronger deduplication and human approval.
- CSV rows are limited to 1000, 40 columns, and 250,000 input characters. Parsing is memory-based, not a hardened upload service.
- Workflow reads, outputs, cancellations, and events are workspace-scoped. Legacy workflows remain hidden until explicitly assigned by a local operator. Retention and document/artifact authorization will be added with those features.
- Python and Node dependencies should be updated and vulnerability-scanned before deployment. No unrestricted shell or network tool is exposed to agents.

## Next checkpoints
1. Checkpoint 05: LLM adapter with structured planner output and strict validation.
2. Checkpoints 06–08: concurrent scheduling, leases, routing, and restricted tools.
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
