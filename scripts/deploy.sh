#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
ENV_FILE="${COMPOSE_ENV_FILE:-.env.production}"
[[ -f "$ENV_FILE" ]] || { echo "Copy .env.production.example to $ENV_FILE and configure it first." >&2; exit 2; }
command -v docker >/dev/null || { echo "Docker Engine and Compose v2 are required." >&2; exit 2; }
command -v curl >/dev/null || { echo "curl is required for post-deploy checks." >&2; exit 2; }
command -v gpg >/dev/null || { echo "GPG is required for encrypted backups." >&2; exit 2; }
docker info >/dev/null

PUBLIC_DOMAIN_VALUE="${PUBLIC_DOMAIN:-$(sed -n 's/^PUBLIC_DOMAIN=//p' "$ENV_FILE" | tail -n 1)}"
PUBLIC_ORIGIN_VALUE="${PUBLIC_ORIGIN:-$(sed -n 's/^PUBLIC_ORIGIN=//p' "$ENV_FILE" | tail -n 1)}"
[[ -n "$PUBLIC_DOMAIN_VALUE" && -n "$PUBLIC_ORIGIN_VALUE" ]] || { echo "Set PUBLIC_DOMAIN and PUBLIC_ORIGIN." >&2; exit 2; }

COMPOSE=(docker compose --env-file "$ENV_FILE" -f compose.yaml -f compose.production.yaml)
"${COMPOSE[@]}" config --quiet
echo "Building and starting the HTTPS staging stack..."
"${COMPOSE[@]}" up -d --build --remove-orphans

for attempt in $(seq 1 60); do
  if curl --fail --silent --show-error "$PUBLIC_ORIGIN_VALUE/healthz" >/dev/null 2>&1 \
    && curl --fail --silent --show-error "$PUBLIC_ORIGIN_VALUE/readyz" >/dev/null 2>&1 \
    && curl --fail --silent --show-error "$PUBLIC_ORIGIN_VALUE/" >/dev/null 2>&1; then
    echo "HTTPS frontend, API liveness, and API readiness checks passed at $PUBLIC_ORIGIN_VALUE."
    exit 0
  fi
  sleep 5
done

echo "Deployment did not become healthy within five minutes. Recent service logs follow." >&2
"${COMPOSE[@]}" ps >&2
"${COMPOSE[@]}" logs --tail=100 api migrate caddy >&2
exit 1
