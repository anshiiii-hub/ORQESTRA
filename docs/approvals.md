# Approvals, human-in-the-loop, and write gating

Checkpoint 12 implements 100% human-in-the-loop write approval enforcement (PRD Section 11, US-06, FR-O08, NFR-08), SHA-256 argument fingerprinting, state machine lifecycle transitions, and role-based write gating.

## Architectural policy and security boundary

1. **Explicit tool classification**:
   - `external_write`: Any tool that modifies durable state outside the volatile task execution context (e.g. `file.export_csv`, `code.apply_patch`, `workspace.config_write`). **100% approval required.**
   - `read_only`: Tools that query or inspect read-only resources (e.g. `web.search`). No approval required.
   - `isolated_compute`: Pure deterministic calculations or isolated ephemeral container checks. No external write side-effects.

2. **100% write approval enforcement (NFR-08)**:
   - When an agent or user invokes an `external_write` tool without an approved token, execution is immediately suspended.
   - The engine issues HTTP 202 `waiting_approval` and registers an `Approval` record with status `pending`.
   - If invoked from a workflow, the workflow status transitions to `waiting_approval`.

3. **Tamper-resistant SHA-256 fingerprinting**:
   - Before human review, all tool parameters are canonicalized (`json.dumps(sort_keys=True, separators=(',', ':'))`) and hashed using SHA-256:
     $$\text{Fingerprint} = \text{SHA-256}(\text{CanonicalJSON}(\text{params}))$$
   - The human reviews sanitized parameters along with the fingerprint.
   - When the approved tool call executes, the engine recomputes the fingerprint of the runtime parameters and asserts:
     $$\text{RuntimeHash} \equiv \text{ApprovedHash}$$
   - Any modification of arguments post-approval triggers immediate rejection with `fingerprint_mismatch`.

4. **Sensitive key redaction**:
   - Keys such as `password`, `token`, `secret`, `api_key`, `credentials`, `private_key`, `auth`, `access_token` are automatically masked to `[REDACTED]` in persisted approval review details.

5. **Approval lifecycle and auto-expiration**:
   - `pending`: Awaiting human review. Expires after 24 hours.
   - `approved`: Authorized by a workspace member or owner. Ready for execution.
   - `rejected`: Explicitly rejected by an authorized human with an audit reason.
   - `expired`: Unreviewed requests past their 24-hour expiration threshold are transitioned to `expired`. Associated workflows transition to `failed`.

6. **Workspace-scoped RBAC**:
   - Only workspace `owner` and `member` roles can approve or reject requests.
   - `viewer` roles can inspect approval requests and history, but cannot submit decisions or execute gated tools.
   - Multi-tenant isolation guarantees approval IDs from workspace A cannot authorize execution in workspace B.

## State machine transitions

```
                     +---------------------------------------+
                     |  Tool Invocation (external_write)     |
                     +---------------------------------------+
                                         |
                                         v
                         +-------------------------------+
                         |   Status: PENDING             |
                         |   Fingerprint: SHA-256        |
                         |   Workflow: WAITING_APPROVAL  |
                         +-------------------------------+
                                 /       |       \
       [Member Approves]        /        |        \  [Member Rejects]
                               /         |         \
                              v          |          v
         +------------------------+      |      +------------------------+
         | Status: APPROVED       |      |      | Status: REJECTED       |
         | Ready for execution    |      |      | Workflow / task fails  |
         +------------------------+      |      +------------------------+
                      |                  |
           [Gated Execution]             | [Expires > 24h]
                      v                  v
         +------------------------+  +------------------------+
         | Status: EXECUTED       |  | Status: EXPIRED        |
         | Tool result recorded   |  | Workflow / task fails  |
         +------------------------+  +------------------------+
```

## API endpoints

- `GET /api/v1/tools`: Returns catalog of registered tools, declared effects (`external_write`, `read_only`), required permissions, and approval requirements.
- `GET /api/v1/approvals?workspace_id=...&status=...`: Lists approvals for a workspace, ordered chronologically. Automatically expires stale requests past the 24-hour window.
- `GET /api/v1/approvals/{approval_id}`: Retrieves detailed approval status, requester and decider emails, sanitized parameters, and fingerprint.
- `POST /api/v1/approvals/{approval_id}/decision`: Accepts `{"decision": "approved" | "rejected", "reason": "..."}`. Enforces owner/member RBAC and updates linked tool calls.
- `POST /api/v1/tools/execute`: Executes a tool with write gating policy:
  - If `read_only`: executes immediately and returns HTTP 200.
  - If `external_write` without approval: suspends, creates pending approval, and returns HTTP 202 `waiting_approval`.
  - If `external_write` with valid approval: verifies non-expired status, matches SHA-256 argument fingerprint, executes, and records execution audit log.
