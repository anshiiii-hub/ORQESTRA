# ORQESTRA AI — Final project report draft

## Abstract

ORQESTRA AI is an academic prototype for authenticated, durable orchestration of deterministic and optional language-model-assisted tasks. A FastAPI service validates typed task plans and persists workflow state in PostgreSQL. A polling worker claims tasks using leases, while a React dashboard shows progress, events, artifacts, approvals, and workspace metrics. The prototype includes adaptive routing, bounded research and data tools, reviewed code proposals, document retrieval, deliverable synthesis, and verification.

The project emphasizes explicit trust boundaries and reproducible behavior. Offline planning and deterministic CSV agents are the default. The Checkpoint 15 evaluation contains 78 frozen CSV cases run in three or more replicates against direct, fixed, and adaptive dispatch. It measures fixture agreement and local sequential dispatch; it does not establish live-model quality or routing superiority. Staging deployment assets provide HTTPS reverse proxying, secret-file based database credentials, health checks, and encrypted database backup/restore procedures. A fresh-host deployment has not yet been validated in this environment.

## Problem and objectives

Agent workflows can lose state after process failure, execute incompatible tools, expose data across tenants, or produce unverifiable outputs. The project objectives were to make workflow inputs typed, execution durable, task dependencies explicit, sensitive writes approval-gated, data access workspace-scoped, and output limitations visible. The implementation is a prototype and not a production autonomous coding service.

## System design

The browser connects to a React/TypeScript dashboard. FastAPI authenticates cookie sessions, validates CSRF/origin for writes, applies workspace membership checks, and persists work in PostgreSQL. Worker processes claim ready tasks through time-limited leases and store attempts/events. Pure task retries are recoverable at task boundaries. Generated reports, charts, datasets, approval records, research provenance, and document chunks are scoped to workspaces.

See [the architecture diagram](architecture.md) for the deployment and service boundaries. PostgreSQL is the shared data store for workflow state, the task queue, and login throttling. The production overlay routes public web traffic through Caddy; application/database ports are not host-published.

## Implementation and controls

- Pydantic task contracts, DAG cycle/dependency checks, schema validation, and capability-aware agent registration.
- PostgreSQL task scheduling with leases, per-attempt state, cancellation fencing, retry, pause/resume, and readiness checks.
- Fixed/adaptive policy with eligibility checks and persisted decision factors. Only one built-in agent currently exists per capability, so the policies produce no meaningful performance comparison.
- Workspace authentication, membership RBAC, CSRF/origin checks, scoped artifact access, and approval fingerprints for external writes.
- Bounded CSV/XLSX analysis, escaped formula-like CSV export, restricted research fetches, isolated code-test sandbox configuration, and untrusted-content labels for retrieval.
- Deliverable synthesis with durable SHA-256 artifacts and a five-tier verification report.
- Checkpoint 15 frozen fixture benchmark and OWASP-informed source/test review; see [the evaluation and security review](checkpoint15-security-review.md).

## Evaluation method and findings

The frozen v2 benchmark contains 78 cases (71 expected-success, 7 expected-failure), spanning numeric CSV variations, missing values, quoted Unicode, formula-like content, prompt-like content, and invalid inputs. Raw fixture bytes are SHA-256 hashed. Each mode supports three to five replicates, defaulting to three. Valid results are recursively compared to expected summaries; expected-invalid cases pass when rejected. Result rows are scoped to a workspace.

The evaluated system is a deterministic in-process pipeline. Reported dispatch duration is sequential local runtime, provider cost is not measured, and parallel speedup is not measured. The confidence interval is descriptive across case-replicates and does not represent independent user samples. No live-model quality claim or adaptive-routing superiority claim is made.

## Security and deployment

The threat model considers unauthorized users, cross-workspace access, prompt-like retrieved data, SSRF, sandbox escape, and unauthorized external writes. Automated checks cover auth/RBAC, research URL filtering, sandbox constraints, approval authorization/fingerprinting, formula-safe exports, and benchmark workspace isolation. A source-and-test review is not a penetration test or certification.

Deployment uses a single-VPS staging profile with Caddy TLS termination, disabled public registration, database-backed login throttling, Python/npm dependency audits in CI, GitHub Dependency Graph and Dependabot alerts, Dependabot update configuration, and secrets mounted from host files. Automatic Dependabot security-update pull requests remain off. The backup script requires GPG encryption and the restore script validates checksums before restoring into a new isolated database. Email verification, password reset, operational alerting, and independent penetration testing remain open. Keep any staging environment behind an IP allowlist or VPN.

## Limitations and future work

The built-in agent catalog is small and deterministic. The system lacks a live-model evaluation set for research, code repair, and synthesis; broad API rate limits; email lifecycle flows; full browser E2E coverage; and live worker kill/restart evidence against PostgreSQL. The sandbox requires a separate TLS-protected Docker Engine and is disabled by default. Staging startup, TLS issuance, real PostgreSQL backup/restore, and live demo acceptance still need a fresh host with DNS and operator credentials.

Future work should add held-out multi-domain evaluation, calibrated competitive routing, a durable queue/checkpointer comparison, frontend E2E tests, independent security testing, monitoring/alert delivery, and deployment rehearsal on a disposable VPS.

## Conclusion

The prototype demonstrates typed orchestration, workspace-scoped state, task leases, approval-gated writes, deterministic verification, and an auditable local evaluation. The implementation favors safe failure and explicit limits. It is suitable as a reproducible academic demonstration after the staging acceptance checklist is completed; current evidence does not support production deployment or comparative performance claims.
