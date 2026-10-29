# Accounts, sessions and workspace roles

Checkpoint 03 replaces the shared development API key with individual accounts. Old `X-API-Key` headers grant no access.

## First use

Open `http://localhost:5173`, create an account with an email address and a password of 12–128 characters, then create a workspace. You become that workspace's owner. Another user must register before the owner can add them by email. The interface supports workspace selection, member/viewer assignment, and removal. Viewer controls are disabled; the server enforces every permission independently.

| Capability | Owner | Member | Viewer |
| --- | --- | --- | --- |
| View workspace workflows, outputs and events | Yes | Yes | Yes |
| Launch/cancel workflows | Yes | Yes | No |
| List workspace members | Yes | Yes | Yes |
| Add, change or remove memberships | Yes | No | No |

The owner cannot be demoted or removed. Ownership transfer and account deletion are not implemented.

## Session behavior

Passwords use uniquely salted scrypt hashes (`N=32768`, `r=8`, `p=3`). Session cookies are opaque random tokens; only their SHA-256 hashes are stored in the database. Cookies are HttpOnly, SameSite=Lax, scoped to `/api/v1`, and expire after 12 hours. Login rotates the current browser session and its CSRF token; logout invalidates it server-side. Expired sessions and inactive accounts are rejected. Expired session records are not automatically purged yet.

The dashboard sends cookies with `credentials: include`. It holds the CSRF token in memory and reloads it from `/auth/me` after a page refresh. It removes the obsolete development key from localStorage.

All mutations require an `Origin` header exactly equal to `APP_ORIGIN`. Authenticated mutations also require the session's `X-CSRF-Token`. Registration and login require the trusted origin before a session exists. API clients must send the same headers and retain the cookie jar. Configure `APP_ORIGIN` to match the browser URL exactly; use `localhost` consistently for frontend and API in local development.

Set `SESSION_COOKIE_SECURE=true` when using HTTPS. Keep it false only for the local HTTP demonstration. `ALLOW_REGISTRATION=false` disables new account registration. These values are passed to the API by Compose. Existing `.env` files may still contain an obsolete `API_KEY`; it is ignored.

Login attempts are counted atomically in the primary database using a SHA-256 digest of the normalized email; raw email addresses are not retained in the throttle table. Five attempts are allowed in a 15-minute fixed window; later attempts receive HTTP 429 with `Retry-After`, and a successful login clears that email's counter. Database errors fail closed with HTTP 503. Old digests are removed during periodic cleanup. This works on the managed PostgreSQL database without requiring a separate Redis service.

## API sequence

All endpoints below have the `/api/v1` prefix:

1. `POST /auth/register` or `/auth/login`: JSON `{email, password}`; returns `{user, csrf_token}` and sets the session cookie.
2. `GET /auth/me`: returns the signed-in user and CSRF token. No password or session token is returned in JSON.
3. `POST /workspaces`: JSON `{name}`; creates an owner membership.
4. `GET /workspaces`: lists only the signed-in user's workspaces and roles.
5. `POST /workspaces/{id}/members`: JSON `{email, role}` where role is `member` or `viewer`; owner only.
6. `DELETE /workspaces/{id}/members/{user_id}`: owner only; revokes membership.
7. `POST /workflows`: JSON `{workspace_id, prompt, csv_text}`; owner/member only.
8. `GET /workflows?workspace_id={id}`: workspace-scoped recent runs.
9. `GET /workflows/{id}`, `/events`, `/stream`: membership checked through the workflow's workspace.
10. `POST /workflows/{id}/cancel`: owner/member only.
11. `POST /auth/logout`: invalidates the session and clears the cookie.

Nonmembers receive 404 for private workspace/workflow IDs. Members with insufficient roles receive 403. Memberships are checked on every request. Event streams also recheck membership, session expiry and account status on each poll, and support `Last-Event-ID` replay. Native EventSource can use `{withCredentials: true}`. The current dashboard still uses REST polling.

## Existing workflows

After `alembic upgrade head`, old workflows are preserved with no workspace assignment and are hidden from all accounts. They are never claimed automatically by the first registered user. After taking a backup, a local database operator can explicitly assign one workflow to an existing workspace:

```sh
docker compose run --rm --no-deps api python -m app.assign_legacy \
  --workflow-id EXISTING_WORKFLOW_ID --workspace-id DESTINATION_WORKSPACE_ID
```

Find the original workflow IDs from the database or saved Checkpoint 01 responses; the destination workspace ID is returned by `GET /api/v1/workspaces`. The command preserves results and events, records an assignment event, and refuses to move a workflow that already belongs to a workspace. There is no public claim endpoint.

## Scope and remaining hardening

This checkpoint is a local academic demonstration. Email verification, password reset, MFA, session-management UI, and production TLS/reverse-proxy hardening remain future work. Registration does not prove ownership of an email address. Do not expose the service publicly before completing those controls.

There are no document/vector/artifact storage APIs yet; workspace isolation for those resources must be added when those features are introduced. Worker processing is a trusted internal service; removal of a submitting user's membership does not automatically cancel an accepted workspace workflow.
