# Checkpoint 16: staging and operations guide

## Scope and deployment boundary

The repository now has a single-VPS staging profile: `compose.yaml` plus `compose.production.yaml`, Caddy automatic HTTPS, a static frontend image, health checks, mounted database credentials, disabled public registration, Redis-backed login throttling, and encrypted database backup/isolated restore scripts. It is not a production approval. Email verification, password recovery, dependency scanning, monitoring/alert delivery, and independent penetration testing are still open controls. Keep staging behind a cloud firewall allowlist or VPN until those controls are reviewed.

This deployment does not enable live LLM planning, research fetching, or code sandboxing. In particular, do not share a production Docker daemon with the sandbox service.

## Fresh VPS preparation

1. Create a supported Linux VPS, attach a DNS A record for the chosen hostname, and allow inbound TCP 80/443. Restrict SSH (22) to operator addresses. Do not expose PostgreSQL, Redis, API, or the Vite development port.
2. Install Docker Engine and the Compose v2 plugin. Install `curl`, `gpg`, and `openssl` on the host. Clone the repository at the release commit.
3. Create operator-only secret files and keep them outside version control:

   ```bash
   cp .env.production.example .env.production
   install -d -m 700 .secrets
   openssl rand -hex 32 > .secrets/postgres_password
   openssl rand -base64 48 > .secrets/backup_passphrase
   chmod 600 .secrets/postgres_password .secrets/backup_passphrase .env.production
   ```

4. Edit `.env.production`: set `PUBLIC_DOMAIN`, matching `PUBLIC_ORIGIN=https://...`, a monitored `ACME_EMAIL`, and the secret paths. Do not put passwords or provider keys in the environment file. Ensure DNS resolves to this host before the first Caddy start.
5. Validate and start the stack:

   ```bash
   docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml config --quiet
   ./scripts/deploy.sh
   ```

   The deploy script waits for the HTTPS frontend, `/healthz`, and `/readyz`. It fails with service logs if they do not become healthy.
6. Create the first account from a password-manager-generated file. This one-time command refuses to run once any user exists:

   ```bash
   ./scripts/bootstrap_admin.sh operator@example.net /secure/path/admin-password
   ```

   The password is read from the mounted file and is not passed as a command-line argument. Registration stays disabled after bootstrap.
7. Sign in, create a workspace, and run the Demo A, B, and C walkthroughs from the dashboard. Read each result banner and the scenario notes below before presenting. Demo D is only a persisted checkpoint-state simulation.

## Backup and restore

Backups use PostgreSQL custom-format dumps and GPG AES-256 symmetric encryption. The passphrase is read from `BACKUP_ENCRYPTION_PASSPHRASE_FILE`; the script refuses to create a plaintext backup. A manifest records the schema revision and counts for users, workspaces, workflows, tasks, and artifacts. SHA-256 checksums cover the dump and manifest. Archive permissions are owner-only by default.

Create a backup and copy the encrypted archive to an independently administered, encrypted off-host location:

```bash
./scripts/backup.sh /secure/local-backups
```

Run a live health and isolated restore drill. It checks HTTPS `/healthz` and `/readyz`, creates an encrypted backup, restores into a new temporary PostgreSQL database, applies current migrations, and compares selected row counts. It removes the temporary restore database on success unless `KEEP_RESTORE_DATABASE=1` is set.

```bash
API_URL=https://your-host.example.net ./scripts/health_drill.sh
```

For a manual restore, use a new database name; the script refuses the production `orchestra` database, existing databases, and unsafe names. Review the target, then provide the exact confirmation token:

```bash
RESTORE_CONFIRM=RESTORE_TO_orqestra_restore_20261027 ./scripts/restore.sh \
  /secure/local-backups/orqestra_20261027T120000Z.tar.gz.gpg \
  orqestra_restore_20261027
```

The restore is kept in the isolated database after validation. To serve from it, schedule downtime, update `DATABASE_NAME` in the operator's production Compose environment, deploy the API/worker against it, validate readiness, and retain the original database until the acceptance window ends. Do not point the restore command at the live database.

Retain multiple dated, off-host encrypted backups and periodically prove that the passphrase and restore process work. A backup left only on the VPS is not disaster recovery.

## Routine operations

- Inspect state: `docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml ps`.
- Readiness: `curl --fail https://your-host.example.net/readyz`.
- Logs: use the same Compose arguments with `logs --tail=200 api worker migrate caddy postgres`.
- Update: take and verify a backup first; check out the approved release commit; validate Compose; rebuild with `./scripts/deploy.sh`; run the health/restore drill.
- Roll back application images by redeploying the previous reviewed commit. Database downgrades are not automatic; use a verified isolated restore and the migration-specific rollback plan.
- Rotate the database credential using a maintenance window: change the PostgreSQL role password interactively with `\password` inside `psql`, atomically replace the mounted secret file, restart migrate/API/worker, and check readiness. Do not put the password in shell history or process arguments.
- Rotate the backup passphrase by making a verified new encrypted backup and preserving old passphrases until all archives encrypted with them expire.
- Review certificate renewal, host disk space, CPU/memory, worker heartbeat, backup age, and restore-drill records at least weekly. The app currently has no alerting service; the operator must monitor these checks externally.

## Demo boundaries for presentation

- **A — Research report:** a persisted local fixture report and chart; source cards and statistics are synthetic. It makes no live network request and must not be cited as current research.
- **B — Coding validation:** a fixture diff plus a real approval record. The displayed test result is scripted and this demo route does not execute the sandbox or modify a repository.
- **C — Dataset intelligence:** runs the real bounded CSV parser/profiler on a synthetic 12-month CSV, computes totals and Z-scores from rows, stores the profiler's SVG chart and report, and records an export approval. It is one synthetic year, not a seasonal forecast.
- **D — Recovery:** stores a five-node checkpoint snapshot and pauses it. It does not kill a live worker; PostgreSQL worker termination/restart evidence remains a separate test gate.

## Acceptance record

Record the release commit, host OS and Docker versions, DNS/TLS result, readiness response, migration revision, health-drill output, encrypted archive hash, restore row-count report, and demo artifact/approval identifiers in the release evidence log. Do not include credentials, passphrases, session cookies, or raw user data.

The fresh-VPS and live PostgreSQL restore acceptance must be run on the actual staging host. A Compose syntax check and local unit suite are not substitutes.
