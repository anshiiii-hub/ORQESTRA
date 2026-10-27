#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
ENV_FILE="${COMPOSE_ENV_FILE:-.env.production}"
EMAIL="${1:-}"
PASSWORD_FILE="${2:-}"
if [[ -z "$EMAIL" || -z "$PASSWORD_FILE" || ! -r "$PASSWORD_FILE" ]]; then
  echo "Usage: $0 <admin-email> <readable-password-file>" >&2
  exit 2
fi
[[ -f "$ENV_FILE" ]] || { echo "Missing deployment environment file: $ENV_FILE" >&2; exit 2; }
PASSWORD_PATH="$(cd "$(dirname "$PASSWORD_FILE")" && pwd)/$(basename "$PASSWORD_FILE")"
docker compose --env-file "$ENV_FILE" -f compose.yaml -f compose.production.yaml run \
  --rm --no-deps -v "$PASSWORD_PATH:/run/secrets/bootstrap_password:ro" api \
  python -m app.bootstrap_user --email "$EMAIL" --password-file /run/secrets/bootstrap_password
