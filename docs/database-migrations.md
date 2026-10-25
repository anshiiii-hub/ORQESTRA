# Database migrations

Schema changes are managed by Alembic. API and worker startup check that the database is at the latest revision; they do not create tables. Compose runs the one-shot `migrate` service before starting either process.

## Fresh database

```sh
docker compose up --build -d
```

For local Python development, run from `backend/` with the intended `DATABASE_URL` set:

```sh
alembic upgrade head
```

## Existing Checkpoint 01 database

An existing unversioned database needs explicit adoption before the first Checkpoint 02 startup. Keep the existing volumes. From the project root:

```sh
mkdir -p backups
docker compose stop api worker
docker compose exec -T postgres pg_dump -U orchestra -d orchestra > backups/before-checkpoint-02.sql
docker compose build api worker
docker compose run --rm --no-deps api python -m app.migrate --adopt-existing
docker compose up --build -d
```

The adoption command compares existing tables, columns, types, indexes, and foreign keys with the Checkpoint 01 models. If they differ, it refuses adoption. If they match, it records revision `0001` without replacing tables or data. It refuses an already versioned database; use `alembic upgrade head` instead. Do not bypass a failed check with a manual stamp. Review the differences and back up before attempting repairs.

Backups contain project data and are ignored by Git. Check the backup command succeeded before proceeding. A backup should be restored into a separate database to verify it before relying on it for recovery.

## Add a migration

From `backend/`, update the SQLAlchemy models, then generate and inspect a new revision:

```sh
alembic revision --autogenerate -m "describe the schema change"
alembic upgrade head
alembic check
```

Commit new migration files; do not edit released revisions. Downgrading the baseline to `base` drops the workflow, task, and event tables. Use downgrade tests only on disposable databases.

## Tests

```sh
cd backend
pytest -q
```

The default integration fixture uses a temporary SQLite database. Set `TEST_DATABASE_URL` only to a dedicated disposable PostgreSQL database. Migration tests upgrade and downgrade that database and remove its application tables during cleanup. Never point this variable at a development database containing work you want to keep.
