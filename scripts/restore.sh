#!/usr/bin/env bash
set -euo pipefail
umask 077

ARCHIVE="${1:-}"
TARGET_DB="${2:-}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
ENV_FILE="${COMPOSE_ENV_FILE:-.env.production}"
PASSPHRASE_FILE="${BACKUP_ENCRYPTION_PASSPHRASE_FILE:-.secrets/backup_passphrase}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/orqestra-restore.XXXXXX")"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f compose.yaml -f compose.production.yaml)
DB_CREATED=0
RESTORE_COMPLETE=0

cleanup() {
  if [[ "$DB_CREATED" == 1 && "$RESTORE_COMPLETE" != 1 ]]; then
    "${COMPOSE[@]}" exec -T postgres dropdb --if-exists -U orchestra "$TARGET_DB" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

if [[ ! -f "$ARCHIVE" || -z "$TARGET_DB" ]]; then
  echo "Usage: $0 <encrypted-backup.tar.gz.gpg> <new-isolated-database-name>" >&2
  exit 2
fi
[[ "$TARGET_DB" =~ ^[a-z][a-z0-9_]{0,62}$ ]] || { echo "Invalid target database name." >&2; exit 2; }
[[ "$TARGET_DB" != "orchestra" && "$TARGET_DB" != "postgres" && "$TARGET_DB" != template* ]] || {
  echo "Refusing restore into the production or a reserved database." >&2; exit 2;
}
[[ "${RESTORE_CONFIRM:-}" == "RESTORE_TO_${TARGET_DB}" ]] || {
  echo "Set RESTORE_CONFIRM=RESTORE_TO_${TARGET_DB} to confirm creation of this isolated database." >&2
  exit 2
}
[[ -f "$ENV_FILE" && -r "$PASSPHRASE_FILE" ]] || { echo "Deployment or encryption secret file is missing." >&2; exit 2; }
command -v gpg >/dev/null || { echo "GPG is required to decrypt this backup." >&2; exit 2; }

echo "Decrypting and validating archive before database changes..."
gpg --batch --pinentry-mode loopback --passphrase-file "$PASSPHRASE_FILE" \
  --decrypt --output "$WORK/backup.tar.gz" "$ARCHIVE"
python3 - "$WORK/backup.tar.gz" "$WORK/unpacked" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath

archive, destination = sys.argv[1:]
with tarfile.open(archive, "r:gz") as source:
    members = source.getmembers()
    if not members or len(members) > 16:
        raise SystemExit("Backup archive has an unexpected number of entries")
    roots = {PurePosixPath(member.name).parts[0] for member in members}
    if len(roots) != 1:
        raise SystemExit("Backup archive must contain exactly one root directory")
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not (member.isdir() or member.isfile()):
            raise SystemExit("Backup archive contains an unsafe path or file type")
        if len(path.parts) > 2:
            raise SystemExit("Backup archive has an unexpected directory layout")
    source.extractall(destination, filter="data")
PY
UNPACKED="$(find "$WORK/unpacked" -mindepth 1 -maxdepth 1 -type d -print -quit)"
[[ -n "$UNPACKED" && -s "$UNPACKED/database.dump" && -s "$UNPACKED/manifest.json" && -s "$UNPACKED/checksums.sha256" ]] || {
  echo "Backup is missing required database or integrity files." >&2; exit 1;
}
if command -v shasum >/dev/null; then (cd "$UNPACKED" && shasum -a 256 -c checksums.sha256); else (cd "$UNPACKED" && sha256sum -c checksums.sha256); fi
python3 - "$UNPACKED/manifest.json" <<'PY'
import json
import sys
from pathlib import Path
manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if manifest.get("project") != "ORQESTRA AI" or manifest.get("backup_format") != 2 or manifest.get("database") != "postgresql":
    raise SystemExit("Unsupported backup manifest")
PY

EXISTS="$("${COMPOSE[@]}" exec -T postgres psql -U orchestra -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname = '$TARGET_DB'" | tr -d '[:space:]')"
[[ "$EXISTS" != 1 ]] || { echo "Target database already exists; refusing to overwrite it." >&2; exit 2; }
"${COMPOSE[@]}" exec -T postgres createdb -U orchestra "$TARGET_DB"
DB_CREATED=1
"${COMPOSE[@]}" exec -T postgres pg_restore --no-owner --exit-on-error -U orchestra \
  --dbname "$TARGET_DB" - < "$UNPACKED/database.dump"
"${COMPOSE[@]}" run --rm --no-deps -e "DATABASE_NAME=$TARGET_DB" migrate
"${COMPOSE[@]}" run --rm --no-deps -e "DATABASE_NAME=$TARGET_DB" api python -c \
  'from app.db import engine; from app.migrate import require_current_schema; require_current_schema(engine); print("Restored database schema is current.")'
SNAPSHOT="$("${COMPOSE[@]}" run --rm --no-deps -e "DATABASE_NAME=$TARGET_DB" api python -m app.ops_snapshot | tr -d '\r')"
python3 - "$UNPACKED/manifest.json" "$SNAPSHOT" <<'PY'
import json
import sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
restored = json.loads(sys.argv[2])
if manifest.get("row_counts") != restored.get("counts"):
    raise SystemExit("Restored table counts do not match the backup manifest")
PY
RESTORE_COMPLETE=1
echo "Restore verified in isolated database: $TARGET_DB"
