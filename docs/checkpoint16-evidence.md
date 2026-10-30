# Checkpoint 16 evidence log

## Local verification

Recorded 27 October 2026 on the development workstation:

| Check | Result |
|---|---|
| Full backend suite (`cd backend && ./.venv/bin/pytest -q`) | 273 passed, 2 skipped |
| PostgreSQL CI integration selection with a disposable local PostgreSQL cluster | 120 passed |
| Ruff, Ruff format check, Mypy | Passed |
| Frontend production build (`cd frontend && npm run build`) | Passed |
| Production Compose merge and exposure assertions | Passed: Caddy alone publishes ports; API, frontend and PostgreSQL have no host ports; registration is disabled; DB password is mounted from a secret file |
| Production deck package/layout validation | Passed: 12 slides, no findings; final PPTX rendered and every slide visually reviewed |
| Isolated local production Compose smoke run | Passed 27 October 2026: images built; PostgreSQL migrated to `0014`; API `/healthz` and `/readyz` passed; frontend served over loopback-only local HTTPS; demos A/C succeeded, B waited for approval, and D paused |
| Encrypted PostgreSQL backup and isolated restore | Passed 27 October 2026: AES-256 GPG archive created, checksums validated, restored to a new database, migrations/current schema verified and manifest row counts matched |
| Fresh VPS deployment and public DNS/TLS | Not run: no staging hostname or operator access exists |
| Off-host encrypted backup copy | Encrypted archive uploaded to the user's Google Drive My Drive on 28 October 2026 and verified at 81 KB; the decryption passphrase remains local, so recovery still depends on that separately stored secret |

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

## Observed hosted workspace check — 28 October 2026

The Blitz-hosted workspace is reachable over HTTPS on release `e11eb00`. The observed `GET /api/v1/readyz` response reported `status: ready`, `database: connected`, schema revision `0014`, and one active worker. The hosted deployment and endpoint are verified; this does not satisfy the fresh-VPS, operator-controlled DNS/TLS, or off-host backup-retention gates below.

The live 5-tier verifier returned `PASS` at 100% calibrated confidence for `sales_numeric_profile.svg` (SHA-256 `c7cf9791122e179e31c10e193b760a0f60a7c9786eafc094717328d54d05fc3b`) and `tax_calculator_validation.patch` (SHA-256 `5d4f5b5cdc62e94cfada06494c19dac5b6a053bf8e310a67b10168142f2c6168`). It also returned `PASS` at 100% for `ev_adoption_india_report.md` (SHA-256 `dc105ae4c998c7a8622f567ee4816ae1147d6fbc84673ab74ca6a1cef12c050c`).

The earlier `sales_anomaly_report_2024.md` artifact (SHA-256 `a80ccb537afe1cbb9e3ffa896d280a9b2450fb2577dffe4e9c71bb7bfbbc908c`) returned `REVISION_REQUIRED` at 50% because its limitations/assumptions disclosure and explicit citation provenance were missing. After rerunning Demo C, the newly generated artifact with the same filename (SHA-256 `14a77ca9483868ba88388ce2a2857c59ecfb93f88270653403b87e2023bda65b`) returned `PASS` at 100% confidence; both criteria are now satisfied. The demo added one pending CSV-export approval (ID `690ee059-3c1b-433d-842d-838fa1c69d08`). No pending write approvals were acted on.

The Blitz database dashboard reported PostgreSQL 17 under 1 MiB with seven nightly copies. A manual 143 KiB `.dump` copy was downloaded, encrypted with GPG AES-256, and saved locally at `backups/orqestra_20261028T094735Z.tar.gz.gpg` (SHA-256 `84f81122c1ebe696039b0181cb44aa95dd9df6eaa4fa51ef87da790de503b288`). On 28 October 2026 the encrypted archive was uploaded to the user's Google Drive My Drive and verified there at 81 KB. The passphrase remains in the Git-ignored `.secrets/backup_passphrase`; neither the passphrase nor the plaintext dump was uploaded or committed. The encrypted archive decrypted successfully, its dump and manifest checksums passed, and the database restored into a second isolated local PostgreSQL 18.3 database. The restored schema was `0014`; counts matched the manifest: 1 user, 1 workspace, 5 workflows, 18 tasks, and 6 artifacts. Off-host archive storage is now in place, but disaster recovery still depends on access to the separately stored passphrase; no independent passphrase escrow or remote restore drill has been verified.

## Repository security settings — 29 October 2026

GitHub repository settings now show the Dependency Graph and Dependabot alerts enabled. Dependabot security-update pull requests remain disabled; weekly version-update PRs are configured in `.github/dependabot.yml`. The latest `main` CI run for commit `23936a6` completed successfully: [workflow run 40673223229](https://github.com/anshiiii-hub/ORQESTRA/actions/runs/40673223229).

## Hosted follow-up — 29 October 2026

The Blitz dashboard showed the app partly asleep, with frontend and API sleeping and the worker running. Its last successful hosted release was `23936a6`; the documentation-only `77572fa` version was queued behind 18 free builders. The pending build requested `REDIS_URL`, while the account's free plan allows one managed database and ORQESTRA already uses it for PostgreSQL. The login throttle was changed to use an atomic PostgreSQL counter instead, avoiding a new paid plan, database, or external service. The resulting release still needs to build before hosted readiness and demo checks can be repeated.

## Fresh hosted backup and restore — 30 October 2026

The signed-in Blitz free-plan dashboard showed the ORQESTRA PostgreSQL 17 database as Ready, under 1 MiB, with seven nightly copies retained. The newest listed copy was from 30 October at 10:58 local time. A fresh provider export, `orqestra-2026-10-30-0729.dump` (196,516 bytes; 188 PostgreSQL archive entries), was downloaded and validated with `pg_restore --list`.

The dump was encrypted with GPG AES-256 using the existing local passphrase and packaged with a manifest and SHA-256 checksums at `backups/orqestra_20261030T073807Z.tar.gz.gpg` (SHA-256 `91ec50313863bb5f6644e217d27d9bb68441c2bc9f3048540d8207cab077f5a1`). Decryption, dump checksum, and manifest checksum verification passed. The archive remains local; the older 28 October encrypted archive is the copy already stored off-host.

The dump restored into the separate local PostgreSQL.app 18.6 database `orqestra_restore_20261030`; the hosted database was not modified. The project schema gate passed at revision `0015`, and the snapshot counts matched the restored database: 2 users, 2 workspaces, 10 workflows, 36 tasks, and 10 artifacts. This verifies a fresh hosted backup and isolated restore, but does not satisfy operator-controlled VPS, public DNS/TLS, independent passphrase escrow, or remote-restore gates.

## Blitz API restart investigation — 30 October 2026

The Blitz app dashboard showed ORQESTRA Online, with no error pages reported for the week, 94% uptime (including free-tier sleep time), and three process crashes since the last start. The saved API output from the 12:34 restart shows application startup failing during `require_current_schema()`: PostgreSQL closed the connection unexpectedly while the API was opening its startup connection. This is a database connection interruption, not evidence of a missing OpenAI key or a schema mismatch. The dashboard showed the managed database Ready and the app Online when checked.

Added a bounded retry to the shared startup schema check: transient SQLAlchemy operational connection errors are retried five times with delays of 1, 2, 4, and 8 seconds before startup fails. Migration-head mismatches and non-connection failures still fail immediately. The change is local and still needs a hosted build and subsequent restart observation before its effect can be accepted.

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
- Checkpoint 16 remains prepared, not signed off, until an operator-controlled fresh-host deployment with public TLS and independent passphrase recovery are addressed. A fresh hosted dump now restores successfully in isolation; the newest encrypted archive remains local, and no remote restore drill has been verified.
- Blitz shows the hosted app Online, but the API retry change has not yet been built there; observe the next startup before marking the restart issue resolved.

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
