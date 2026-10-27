# ORQESTRA AI — implementation status
> **Design and Development of an Adaptive, Fault-Tolerant Multi-Agent LLM Orchestration Framework**

This prototype runs deterministic analysis through a persisted PostgreSQL task graph. It includes optional OpenAI planning, leased task scheduling, fixed/adaptive routing, authenticated research, bounded CSV/XLSX analysis, reviewed code proposals in isolated sandboxes, workspace-scoped document memory, human-in-the-loop write approvals, deliverable synthesis, verification, recovery controls, and a frozen 78-case evaluation suite. Offline planning remains the default and makes no provider calls. The full autonomous coding product remains planned.

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
- Capability routing filters, deterministic weighted scores, fixed/adaptive baselines, and persisted decision factors; see [routing policy](docs/routing.md).
- Authenticated research requests with exact operator-managed host allowlists, public-address DNS pinning, HTTPS-only fetching, blocked redirects, bounded HTML/text extraction, and workspace-scoped provenance; see [restricted research tools](docs/research.md). Retrieved page text is always recorded as untrusted data.
- Workspace-scoped CSV/XLSX imports with schema profiling, deterministic numeric statistics, generated SVG charts, content hashes, and reproducible safe CSV exports; see [data analysis](docs/data-analysis.md).
- Workspace-scoped code patch proposals, fixed Python checks, bounded execution artifacts, and a reviewable unified diff; see [code sandbox](docs/sandbox.md).
- Workspace-scoped document memory, bounded chunk partitioning with exact source character offsets, deterministic 128-dimensional vector embeddings, keyword/hybrid search ranking, and deletion cascades; see [document memory](docs/document-memory.md). Retrieved text is strictly isolated to its owning workspace and treated as untrusted evidence.
- 100% human-in-the-loop write gating for sensitive tools (`file.export_csv`, `code.apply_patch`, `workspace.config_write`), canonical JSON SHA-256 argument fingerprinting to prevent parameter tampering, 24-hour auto-expiration, sensitive parameter masking, and owner/member RBAC decision enforcement; see [write approvals and human-in-the-loop](docs/approvals.md).
- Multi-format deliverable synthesis (Markdown, styled printable HTML, structured JSON), durable SHA-256 fingerprinted artifact storage, secure attachment downloads, and calibrated 5-tier verification rubric (`PASS`, `PASS_WITH_WARNINGS`, `REVISION_REQUIRED`, `FAIL`, `INDETERMINATE`) with defect attribution and mandatory limitations disclosure; see [synthesis and verification](docs/reporting-verification.md).
- Workflow pause/resume/retry controls, a frozen 78-case deterministic evaluation set with three-replicate single/fixed/adaptive baselines, database/schema readiness checks, and workspace telemetry; see [benchmarks and recovery](docs/benchmarks-recovery.md) and the [Checkpoint 15 evaluation and security review](docs/checkpoint15-security-review.md). The evaluation measures fixture agreement and local sequential dispatch; it does not measure provider quality, parallel speedup, or provider cost.
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
- Validation status (26 October 2026): 266 local tests pass; two PostgreSQL-only lease/concurrency tests are skipped locally and included in the PostgreSQL CI job. Ruff, Mypy, migration tests, contract checks, and the frontend production build pass. Live-model quality remains unmeasured; no paid API calls were made.

- **Do not expose this prototype publicly or deploy it to production.** Email verification, password reset, login rate limiting, and production TLS hardening remain future work. See [authentication scope and limitations](docs/authentication.md#scope-and-remaining-hardening).
- Research fetches are disabled by default. Operators may set `RESEARCH_ENABLED=true` and a comma-separated exact host list in `RESEARCH_ALLOWED_DOMAINS`; the research endpoint is bounded to three URLs, 256 KiB per response, and six seconds per request. It accepts caller-provided URLs on approved domains; it does not crawl links or expose a general-purpose browser/search engine. Source excerpts remain untrusted and cannot grant tool permissions.
- Dataset files are capped at 5 MiB, 5,000 rows, 100 columns, and 16,384 characters per cell. XLSX ZIP expansion and archive member counts are bounded. Formulas are read as text and escaped in CSV exports; macro-enabled formats are rejected. Uploaded source files are not retained, while normalized outputs, profiles, chart artifacts, and content hashes are stored in the owning workspace.
- Sandbox execution is disabled by default. It requires a dedicated Docker Engine configured for the API over TLS; never point it at a production or shared daemon. Each code run uses a pinned local image, no network, no host mounts, a read-only root filesystem with bounded tmpfs, a non-root UID, dropped capabilities, no-new-privileges, 256 MiB memory, 0.5 CPU, 32 PIDs, and a 35 second wall cap. Only fixed `pytest`, `unittest`, and `compileall` checks are accepted. The Docker daemon must enforce cgroup v2 resource controls. See [sandbox setup and limitations](docs/sandbox.md).
- Reference documents are capped at 5 MiB and 1,000,000 characters. Uploads accept text, markdown, source code, data, and text-bearing PDFs. Path traversal, null bytes, and unauthorized formats are rejected. Documents and chunks are strictly isolated to their workspace, and document deletion cascades to immediate index cleanup.
- Sensitive external write tools require 100% human-in-the-loop review. Execution is paused into `waiting_approval` with SHA-256 parameter fingerprinting. Modifying parameters after review causes a fingerprint mismatch. Approvals expire after 24 hours. Viewers cannot approve or reject actions.
- Synthesized deliverables are stored with SHA-256 content fingerprints and verified against the 5-Tier Verification Rubric. External evaluation is strictly segregated from model self-assessment, and failures in upstream tasks or absence of mandatory limitations disclosures prevent `PASS` grading (FR-V04, FR-V05).
- Resumption from checkpoint boundaries freezes and preserves all completed node results, replaying only eligible unfinished tasks. Workers ignore paused workflows and recover expired leases cleanly upon worker restart (Demo D).
- Passwords use salted scrypt. Session cookies are HttpOnly and SameSite=Lax; authenticated writes require a session-bound CSRF token and trusted origin. Enable secure cookies for HTTPS.
- The UI polls REST every ~1.8 seconds. Optional SSE uses the session cookie, supports event cursors, and rechecks access during streaming.
- Alembic manages the schema. API and worker startup require the current migration revision. Existing unversioned databases require explicit, schema-checked adoption; see [migration instructions](docs/database-migrations.md).
- Workers claim workflow rows briefly with `FOR UPDATE SKIP LOCKED`, persist a task attempt and lease before dispatch, and execute outside database locks. Task results publish only while the current attempt still owns the lease. The lease lasts 360 seconds (longer than the 300-second contract timeout). Execution uses at-least-once recovery: a task may run again after its lease expires, so agents must remain idempotent and free of external side effects.
- Pure CSV tasks may requeue after restart. A potentially billable planning request is not replayed when its outcome is uncertain; its reservation is retained and the workflow fails explicitly. External write tools still require stronger deduplication and approval.
- LLM planning sends task instructions, CSV column names and row count to the configured provider; CSV row values stay local. Planning costs are estimates from operator-maintained price snapshots, not an invoice cap.
- CSV rows are limited to 1000, 40 columns, and 250,000 input characters. Parsing is memory-based, not a hardened upload service.
- Workflow reads, outputs, cancellations, and events are workspace-scoped. Legacy workflows remain hidden until explicitly assigned by a local operator. Retention and document/artifact authorization will be added with those features.
- Python and Node dependencies should be updated and vulnerability-scanned before deployment. No unrestricted shell or network tool is exposed to agents.

## Checkpoint status
Repository feature numbers do not match the project handbook's 16-checkpoint plan. The dashboard now includes a dependency-aware DAG, replaying event stream, recent-workflow filtering, and workspace status analytics, but keyboard/SSE-reconnect E2E coverage and search beyond the latest 50 workflows remain outstanding; see [dashboard observability](docs/dashboard-observability.md). Recovery controls do not yet provide the handbook's LangGraph PostgreSQL checkpointer or live worker-kill/restart evidence. Checkpoint 15 now includes a frozen 78-case, three-replicate evaluation and an OWASP-informed source-and-test security review; it is not a third-party penetration test or provider quality evaluation. Cloud deployment/final submission gates remain incomplete; see [checkpoint alignment and current limitations](docs/benchmarks-recovery.md).

## Repository
```
backend/app/  FastAPI API, models, planner, agents and worker
backend/tests/  DAG, CSV, migrations, auth, protocol and runtime tests
backend/contracts/  Versioned JSON Schema exports
frontend/src/  React dashboard
compose.yaml    Local services
.env.example    Configuration template
```
