# Checkpoint 15: evaluation and security review

## Evaluation protocol

The checked-in dataset is `backend/app/fixtures/benchmark_v2.json` (78 cases: 71 expected-success and 7 expected-failure cases). Each run computes SHA-256 over the exact fixture bytes and stores it with every result. A changed fixture cannot silently reuse the frozen v2 suite. Each selected mode runs every case three times by default; the API allows three to five replicates. Results are scoped to the requesting workspace, and summary/detail reads enforce membership.

Modes are:

- **Single:** direct selection from the local agent registry, without the routing policy.
- **Fixed:** dispatch through fixed routing.
- **Adaptive:** dispatch through adaptive routing.

Scores use exact recursive fixture comparison of the verified summary, with a small numeric tolerance. Expected-invalid cases pass when the pipeline rejects the input. The fixture contains missing values, signed/decimal numbers, quoted Unicode, formula-like text, and quoted prompt-injection-like text. Timings cover local sequential in-process dispatch only. The current registry has one agent per capability, so fixed and adaptive select the same agents. `parallel_speedup` is intentionally unavailable. Provider cost is not measured because this suite makes no provider calls. Wilson intervals are descriptive intervals over case-replicate outcomes; cases are not independent user samples.

The API persists case-replicate observations in one transaction per execution. `status` on a result means that the observed behavior matched its fixture expectation; actual pipeline status and failure code are retained separately in result metadata. Historical pre-v2 results remain unscoped and are excluded from workspace summaries.

## Threat model and review scope

Assets include workspace benchmark inputs/results, authentication sessions and CSRF tokens, approval decisions, uploaded documents, research provenance, and the host environment used by the optional code sandbox. Relevant actors include unauthenticated callers, authenticated users in another workspace, viewers attempting writes, prompt-like content embedded in CSV/documents/research results, malicious research URLs, and untrusted proposed code. Trust boundaries are the session/API boundary, workspace membership checks, text/data ingestion, external network fetches, approval-gated writes, and the isolated container boundary.

The review used OWASP-inspired checks for access control, injection/data trust, SSRF, unsafe execution, and sensitive write authorization. It was a source-and-test review, not a third-party penetration test or formal OWASP ASVS certification.

| Concern | Existing or added evidence |
|---|---|
| Workspace isolation and role enforcement | `backend/tests/test_auth_rbac.py`, `backend/tests/test_memory.py`, and Checkpoint 15 benchmark isolation tests |
| Session, CSRF, and origin checks | `backend/tests/test_auth_rbac.py`; implementation in `backend/app/auth.py` |
| Login throttling | Redis-backed atomic counter: five attempts per normalized email per 15-minute window; production Compose configures Redis and the API fails closed if Redis is unavailable |
| Dependency security checks | CI audits pinned Python requirements with `pip-audit` and frontend lockfile dependencies with `npm audit`; Dependabot is configured for weekly Python, npm, Docker, and GitHub Actions updates |
| Prompt-like CSV text stays input data | Frozen prompt-like CSV fixture and expected-output evaluation in `backend/tests/test_checkpoint_recovery_benchmarks.py`; the deterministic agents do not interpret cell values as instructions |
| Research SSRF boundary | `backend/tests/test_research.py`; exact host allowlists, public-address checks, HTTPS and redirect restrictions in `backend/app/research.py` |
| Untrusted code execution | `backend/tests/test_sandbox.py`; container hardening and disabled-by-default requirements in `docs/sandbox.md` |
| Sensitive writes and tamper resistance | `backend/tests/test_approvals.py`; fingerprint, expiration, and role enforcement in `backend/app/approvals.py` |
| Spreadsheet formula export | `backend/tests/test_data_analysis.py`; formula escaping and bounded parsing in `backend/app/data_analysis.py` |
| Benchmark results cannot cross workspaces | Added owner-A/owner-B execution, summary, and detail access checks in `backend/tests/test_checkpoint_recovery_benchmarks.py` |

## Findings and remaining risk

- The benchmark endpoints now require workspace membership; execution additionally requires owner/member. Summary and detail are scoped by `workspace_id`. Historical results without a workspace remain inaccessible through these workspace endpoints.
- Deterministic fixtures establish implementation behavior, not LLM resistance to prompt injection. The current suite is offline and does not test provider behavior or externally supplied model output.
- Email verification, password reset, production TLS/secret rotation, repository-level Dependabot alerts, and operational monitoring remain incomplete; see `docs/authentication.md`.
- Research and sandbox features are opt-in and retain their documented network, Docker, size, and runtime boundaries. Local Docker is unavailable in this environment, so sandbox runtime isolation was not re-exercised here.
- The local test environment uses SQLite. PostgreSQL-only lease/concurrency tests depend on the PostgreSQL CI job. No live infrastructure penetration test, dependency vulnerability scan, or production deployment test was performed as part of this checkpoint.

No claim of production security or measured adaptive-routing superiority is made.
