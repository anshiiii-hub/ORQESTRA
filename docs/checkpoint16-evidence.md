# Checkpoint 16 evidence log

## Local verification

Recorded 27 October 2026 on the development workstation:

| Check | Result |
|---|---|
| Full backend suite (`cd backend && ./.venv/bin/pytest -q`) | 273 passed, 2 skipped |
| PostgreSQL CI integration selection with a disposable local PostgreSQL cluster | 120 passed |
| Ruff, Ruff format check, Mypy | Passed |
| Frontend production build (`cd frontend && npm run build`) | Passed |
| Production Compose merge and exposure assertions | Passed: Caddy alone publishes ports; API, frontend, PostgreSQL and Redis have no host ports; registration is disabled; DB password is mounted from a secret file |
| Production deck package/layout validation | Passed: 12 slides, no findings; final PPTX rendered and every slide visually reviewed |
| Isolated local production Compose smoke run | Passed 27 October 2026: images built; PostgreSQL migrated to `0014`; API `/healthz` and `/readyz` passed; frontend served over loopback-only local HTTPS; demos A/C succeeded, B waited for approval, and D paused |
| Encrypted PostgreSQL backup and isolated restore | Passed 27 October 2026: AES-256 GPG archive created, checksums validated, restored to a new database, migrations/current schema verified and manifest row counts matched |
| Fresh VPS deployment, public DNS/TLS and off-host backup retention | Not run: no staging hostname, operator access or off-host storage target exists |

The two skipped cases require the PostgreSQL environment variable in the CI workflow; the same workflow's PostgreSQL integration selection was run locally against a disposable database and passed. The local Compose smoke run binds ports only to `127.0.0.1` and does not establish public TLS, off-host backup retention or cloud acceptance.

## API response examples

These are response shapes from the checked-in API implementation and tests. Capture the actual HTTPS responses during staging acceptance before treating the deployment gate as passed. Timestamps and migration revision values vary by release.

`GET /healthz`:

```json
{
  "status": "ok",
  "service": "orqestra-api",
  "version": "1.0.0",
  "timestamp": "<server timestamp>"
}
```

`GET /readyz` when the database is reachable and the schema matches:

```json
{
  "status": "ready",
  "database": "connected",
  "schema_revision": "<current Alembic head>",
  "active_workers": 0,
  "timestamp": "<server timestamp>"
}
```

The guided demo launch endpoint returns the persisted workflow ID and the scenario's state. The API tests assert Demo A and C finish as `succeeded`, Demo B pauses as `waiting_approval`, and Demo D becomes `paused`; tests also verify workspace scoping and scenario artifact/approval records. See `backend/tests/test_checkpoint16_demos.py`.

## Failure and fix log

| Observation | Cause | Fix or disposition |
|---|---|---|
| Recent GitHub commits showed 1 of 2 checks failing (as in the project screenshot). | The PostgreSQL CI job reproduced the failure: a readiness test changed `alembic_version` to simulate an outdated schema but did not restore it. Fixture teardown then downgraded from a false revision, leaving newer foreign keys/tables behind and causing follow-on migration errors. | Restore the actual current Alembic head in a `finally` block. Add a migration round-trip assertion for removal of the checkpoint-15 workspace column and its foreign key. The CI-equivalent PostgreSQL selection now passes locally (120 tests). Confirm the new commit's hosted run after push. |
| First local presentation render wrapped two long headings and showed reversed connector arrowheads. | The original title lengths exceeded the slide text boxes, and the connector's `head` property drew the arrowhead at the opposite end. | Shortened the headings, removed the misleading arrowheads, rerendered the final 12-slide deck and visually inspected every slide. |
| The first PostgreSQL demo run failed in Demo B and Demo C. | Each scenario created an approval referencing a task before PostgreSQL had persisted that task; Demo C also wrote artifacts referencing a task before it was flushed. SQLite-backed tests had not exposed the foreign-key ordering issue. | Persist each referenced task before creating its approval or artifact. Add assertions for approval and artifact task links. The PostgreSQL local smoke run now passes all four demos. |
| Initial local restore rejected a newly generated macOS backup archive. | The system `tar` added AppleDouble `._*` metadata entries, which correctly failed the restore path validator. | Set `COPYFILE_DISABLE=1` for archive creation. The resulting encrypted archive passed path and checksum validation. |
| The next local restore stopped at PostgreSQL dump loading. | `pg_restore` treated the explicit `-` argument as a filename even though Compose already supplied the dump on standard input. | Omit the filename argument and read stdin. The end-to-end restore then migrated and verified the new database and matched manifest row counts. |

## Design decisions

- The staging overlay exposes Caddy only and disables public registration and optional external/LLM/sandbox features.
- Database credentials enter through a mounted secret file. The one-time bootstrap command refuses to create an account after the first user exists.
- Backup archives require GPG encryption and include a manifest and checksums. Restore creates a new database, rejects the live database, applies migrations and checks row counts.
- Guided demo boundaries are explicit: A and B use fixtures, C executes the bounded analyzer against synthetic data, and D is a persisted state simulation.
- Checkpoint 16 remains prepared, not signed off, until a fresh-host deployment, public TLS and off-host backup retention are recorded by the operator. The local encrypted backup and isolated restore flow is verified.

## Staging acceptance record

Fill this section from the actual staging host. Do not copy credentials, private data, or session cookies into the log.

- Release commit/tag:
- Host OS and Docker/Compose versions:
- DNS/TLS result and certificate expiry:
- `/healthz` and `/readyz` response captures:
- Migration revision and worker health:
- Demo A/B/C workflow and artifact IDs:
- Encrypted archive SHA-256 and off-host location:
- Restore target, schema and row-count result:
- External security/dependency review:
