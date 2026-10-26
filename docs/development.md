# Development and checks

Use Python 3.12 and Node.js 22 to match CI and the container images.

## Backend

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r backend/requirements-dev.txt
pre-commit install
cd backend
ruff check .
ruff format --check .
mypy
python -m app.export_contracts --check
pytest -q
```

Mypy checks all application modules, including the bodies of untyped functions. Ruff checks imports and Python correctness and enforces consistent formatting. Integration fixtures exercise migrations and the authenticated API-to-worker path. See [database migrations](database-migrations.md) before starting against existing data.

## Frontend

```sh
cd frontend
npm ci
npm run build
```

## Update dependency locks

Update direct dependencies in `backend/requirements.in` or `backend/requirements-dev.in`. Compile both locks with Python 3.12 using the pinned pip-tools version:

```sh
cd backend
pip-compile --strip-extras --generate-hashes --no-emit-index-url --no-emit-trusted-host requirements.in
pip-compile --allow-unsafe --strip-extras --generate-hashes --no-emit-index-url --no-emit-trusted-host requirements-dev.in
```

For deliberate transitive updates, add `--upgrade` and review the resulting versions and hashes. Reinstall with `--require-hashes` and rerun checks. When frontend dependencies change, regenerate `package-lock.json` with Node 22 and verify `npm ci` and `npm run build`. Docker uses the committed locks.

## CI

GitHub Actions runs on pushes and pull requests. Backend checks include Ruff lint/format, Mypy, a contract-schema drift check, the SQLite suite, and migration/API/agent-runtime/planning/worker-lease integration against PostgreSQL 16. The PostgreSQL job includes a barrier test proving two independent ready tasks overlap across workers. The frontend job installs from its lockfile and builds the production bundle. A failed check or test fails its job. Repository administrators can require these checks through GitHub branch protection.

`TEST_DATABASE_URL` is exclusively for disposable test databases: migration tests drop their tables. CI provisions its own PostgreSQL service. The normal development database is not used by the integration fixture unless this variable is explicitly pointed at it.

The planning suite uses mocked HTTP and replay providers; it does not need an API key or make paid calls. The 20-case corpus tests graph acceptance/rejection and bounded repair, not live-model quality. See [LLM planning](llm-planning.md) for explicit opt-in configuration and budget limitations.
