#!/usr/bin/env bash
set -euo pipefail
umask 077

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
ROOT="${1:-${BACKUP_ROOT:-./backups}}"
ENV_FILE="${COMPOSE_ENV_FILE:-.env.production}"
PASSPHRASE_FILE="${BACKUP_ENCRYPTION_PASSPHRASE_FILE:-.secrets/backup_passphrase}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
NAME="orqestra_${STAMP}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/${NAME}.XXXXXX")"
ARCHIVE="${ROOT}/${NAME}.tar.gz.gpg"
ARCHIVE_TMP="${ARCHIVE}.tmp"
PLAIN_ARCHIVE="${WORK}.tar.gz"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f compose.yaml -f compose.production.yaml)

cleanup() { rm -rf "$WORK"; rm -f "$ARCHIVE_TMP" "$PLAIN_ARCHIVE"; }
trap cleanup EXIT

[[ -f "$ENV_FILE" ]] || { echo "Missing deployment environment file: $ENV_FILE" >&2; exit 2; }
[[ -r "$PASSPHRASE_FILE" ]] || { echo "Missing backup encryption passphrase file: $PASSPHRASE_FILE" >&2; exit 2; }
command -v docker >/dev/null || { echo "Docker Compose is required for a PostgreSQL backup." >&2; exit 2; }
command -v gpg >/dev/null || { echo "GPG is required; refusing to create a plaintext backup." >&2; exit 2; }
mkdir -p "$ROOT"
chmod 700 "$ROOT"

echo "Creating PostgreSQL custom-format dump..."
"${COMPOSE[@]}" exec -T postgres pg_dump -U orchestra -d orchestra --format=custom > "$WORK/database.dump"
[[ -s "$WORK/database.dump" ]] || { echo "Database dump is empty; refusing to continue." >&2; exit 1; }

SNAPSHOT="$("${COMPOSE[@]}" exec -T api python -m app.ops_snapshot | tr -d '\r')"
python3 - "$WORK/manifest.json" "$STAMP" "$SNAPSHOT" <<'PY'
import json
import platform
import sys
from pathlib import Path
snapshot = json.loads(sys.argv[3])
Path(sys.argv[1]).write_text(json.dumps({
    "project": "ORQESTRA AI",
    "backup_format": 2,
    "created_at_utc": sys.argv[2],
    "database": "postgresql",
    "schema_revisions": snapshot["schema_revisions"],
    "row_counts": snapshot["counts"],
    "host_platform": platform.platform(),
}, indent=2) + "\n", encoding="utf-8")
PY

if command -v shasum >/dev/null; then
  (cd "$WORK" && shasum -a 256 database.dump manifest.json > checksums.sha256)
else
  (cd "$WORK" && sha256sum database.dump manifest.json > checksums.sha256)
fi
mkdir -p "$ROOT"
COPYFILE_DISABLE=1 tar -czf "$PLAIN_ARCHIVE" -C "$(dirname "$WORK")" "$(basename "$WORK")"
gpg --batch --yes --pinentry-mode loopback --passphrase-file "$PASSPHRASE_FILE" \
  --symmetric --cipher-algo AES256 --output "$ARCHIVE_TMP" "$PLAIN_ARCHIVE"
chmod 600 "$ARCHIVE_TMP"
mv "$ARCHIVE_TMP" "$ARCHIVE"
echo "Encrypted backup created: $ARCHIVE"
if command -v shasum >/dev/null; then shasum -a 256 "$ARCHIVE"; else sha256sum "$ARCHIVE"; fi
