# Completion record — 2 November 2026

## Selected release scope

The user selected the free hosted demonstration scope on 2 November 2026. Live LLM planning and isolated code execution are outside this release. Research and coding walkthroughs remain explicitly labelled. No paid provider or new infrastructure is required.

## Implemented and verified in this pass

- Separate agent application destinations, workflow tabs, report synthesis and artifact verification are present in the frontend. The production build compiles.
- Workflow history searches all workspace records with status filters and bounded pagination. Tests cover records beyond the first 50, literal SQL wildcard input, invalid page bounds, and access isolation.
- Same-origin authenticated SSE URLs now resolve correctly instead of throwing when the API base is empty.
- The PostgreSQL worker lease comparison preserves timezone-aware timestamps. A real subprocess is killed after a durable claim, its lease is expired in the disposable acceptance database, and a new process resumes unfinished work without replaying completed results. A second case proves cancellation survives restart. The test forces an Asia/Kolkata database session to exercise the timezone defect.
- PostgreSQL CI includes the process-termination and full-history search acceptance cases.

## Architecture choice

The implementation uses the custom Python DAG engine described as an alternative in handbook Part F, backed by PostgreSQL task attempts, leases, checkpoints and audit events. It does not use LangGraph or its PostgreSQL checkpointer. This is an explicit technology substitution, not evidence that the LangGraph-specific implementation step was performed.

## Remaining acceptance and product scope

- Local browser acceptance passed: account/workspace creation with synthetic data, a three-task workflow to success, live-to-complete event status, full-history navigation, and keyboard activation of the coding workspace. The API was killed and restarted; the browser changed from reconnecting to live automatically. These are manual browser checks, not automated browser E2E coverage.
- Final hosted release health still needs verification after deployment.
- The research application needs operator-approved domains before live requests can run. Its examples remain labelled fixtures.
- Coding execution needs a dedicated isolated Docker Engine; it is disabled on the managed host. The coding example does not execute a sandbox or edit a repository.
- Live LLM planning is optional and disabled without a configured provider. Current built-in workflow agents are deterministic CSV inspection, summarization and verification. No live-model quality result is claimed.
- Fresh-machine VPS deployment, remote restore acceptance and independent backup-passphrase recovery remain operator tasks. Existing managed hosting and isolated local restore evidence do not prove these gates.
- Public-production readiness still needs account recovery/email verification, external alert delivery, and independent security review. These are distinct from the college demonstration release.

Do not label the complete handbook or a full autonomous coding product as finished until these acceptance items are resolved or an explicit academic scope substitution is approved.

## Validation results

- Backend suite: 282 passed, 4 PostgreSQL-only cases skipped in the SQLite run.
- PostgreSQL CI selection: 124 passed, including worker process termination/restart, cancellation and search.
- Ruff, formatting and Mypy passed. Frontend TypeScript/Vite production build passed.
