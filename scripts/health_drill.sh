#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
: "${API_URL:?Set API_URL to the HTTPS staging origin before running this drill.}"
ENV_FILE="${COMPOSE_ENV_FILE:-.env.production}"
DRILL_DIR="$(mktemp -d "${TMPDIR:-/tmp}/orqestra-health-drill.XXXXXX")"
RESTORE_DB="orqestra_restore_drill_$(date -u +%Y%m%d%H%M%S)"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f compose.yaml -f compose.production.yaml)
RESTORE_CREATED=0

cleanup() {
  if [[ "$RESTORE_CREATED" == 1 && "${KEEP_RESTORE_DATABASE:-0}" != 1 ]]; then
    "${COMPOSE[@]}" exec -T postgres dropdb --if-exists -U orchestra "$RESTORE_DB" >/dev/null 2>&1 || true
  fi
  rm -rf "$DRILL_DIR"
}
trap cleanup EXIT

echo "Checking public liveness endpoint..."
curl --fail --silent --show-error "$API_URL/healthz" >/dev/null
echo "Checking public readiness endpoint..."
curl --fail --silent --show-error "$API_URL/readyz" >/dev/null
echo "Creating encrypted database backup..."
./scripts/backup.sh "$DRILL_DIR"
ARCHIVE="$(find "$DRILL_DIR" -maxdepth 1 -type f -name '*.tar.gz.gpg' -print -quit)"
[[ -n "$ARCHIVE" && -s "$ARCHIVE" ]] || { echo "Backup command produced no archive." >&2; exit 1; }
echo "Restoring and count-verifying in an isolated temporary database..."
RESTORE_CONFIRM="RESTORE_TO_${RESTORE_DB}" ./scripts/restore.sh "$ARCHIVE" "$RESTORE_DB"
RESTORE_CREATED=1
echo "Readiness, encrypted backup, and isolated restore verification passed."
