# ORQESTRA AI viva pack

## 8-minute presentation outline

| Time | Slides | Presenter focus |
|---|---|---|
| 0:00–0:35 | 1 | State the problem: reliable execution around agents, not autonomous intelligence alone. |
| 0:35–1:20 | 2 | Explain the three objectives: executable plans, durable progress, reviewable results. |
| 1:20–2:15 | 3 | Trace browser → Caddy → API → PostgreSQL and worker → PostgreSQL. Explain Redis is not in the current dispatch path. |
| 2:15–3:00 | 4 | Walk through validation, persistence, leases, dispatch, verification and result fencing. Emphasize at-least-once semantics. |
| 3:00–3:45 | 5 | Describe workspace authorization, approval fingerprints, untrusted retrieved text and optional sandbox isolation. |
| 3:45–5:00 | 6–7 | Explain the frozen 78-case fixture design and what its results can and cannot support. |
| 5:00–6:00 | 8 | Run Demo A, B and C. Label A/B as fixtures and C as the real bounded analyzer. Mention D is a state simulation. |
| 6:00–7:00 | 9–10 | Show the staging architecture and backup/restore flow. State that a live host acceptance has not yet been run. |
| 7:00–8:00 | 11–12 | Separate passed local checks from open gates; close with trade-offs and invite questions. |

## Live demo sequence

1. Sign in and select a workspace.
2. Open **Run a showcase scenario** and launch Demo A. Show the report artifact and point out the synthetic source-card warning.
3. Launch Demo B. Show the approval record and sample patch; do not say the isolated sandbox ran.
4. Launch Demo C. Show the synthetic dataset report, computed values, and SVG profile chart.
5. Optionally show Demo D's paused checkpoint snapshot. Explain that no live worker was terminated.
6. Return to a normal offline workflow and inspect its graph, task states and output.

Avoid enabling LLM planning, research fetching, or code sandboxing during the viva unless their operator configuration has been separately reviewed and tested.

## Likely questions and concise answers

**Why use PostgreSQL for task state?**

It provides durable workflow/task state and transactional worker claims. The worker persists lease ownership and publishes a result only if that attempt still owns the task.

**Does the scheduler provide exactly-once execution?**

No. A task can run again after lease expiry. The design uses at-least-once recovery, stale-result fencing and pure/idempotent tasks where possible. External side effects need stronger deduplication and approval.

**Why is Redis in the deployment?**

Redis is provisioned but not part of the current execution path. PostgreSQL remains the source of truth for worker claims. Redis is future infrastructure, not a hidden dependency.

**What did the benchmark establish?**

It checks fixture agreement on 78 deterministic CSV cases with repeated runs. It does not establish live model quality, cost, parallel speedup or adaptive routing superiority. Fixed and adaptive select the same agent today because only one built-in agent is registered per capability.

**How is prompt injection handled?**

Retrieved text is marked untrusted and does not grant tool permissions. The prompt-like CSV fixture checks deterministic data handling only; there is no claim of provider-level prompt-injection resistance.

**What is the status of deployment?**

The repository contains a staging Compose overlay, TLS proxy, secret-file injection, bootstrap procedure, and encrypted backup/isolated restore scripts. Fresh VPS deployment and live restore acceptance need a configured host, domain, DNS and operator-controlled secrets; those checks are not reported as passed.

**What remains?**

PostgreSQL worker kill/restart acceptance, browser E2E for reconnect and keyboard access, older-workflow search, independent security/dependency review, operational alerting, and authentication lifecycle controls.

## Evidence to bring

- Commit IDs for the release and checkpoint milestones.
- Latest full local test output and CI status.
- A real staging `/healthz` and `/readyz` response after deployment.
- Encrypted backup archive hash and a successful isolated restore record.
- Demo workflow, artifact and approval identifiers from the staging run.
- A failure-and-fix log; never include secrets, session cookies or private source data.
