#!/usr/bin/env bash

set -Eeuo pipefail

VERSION="1.1.0"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "Required command not found: $1"
  fi
}

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  fail "Neither 'docker compose' nor 'docker-compose' is available"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VM_DIR="$REPO_DIR/vm"
COMPOSE_FILE="$VM_DIR/docker-compose.yml"
COMPOSE_ENV_FILE="$VM_DIR/.env"
TARGET_BRANCH="${1:-main}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-https://localhost:8443/health}"
STATUS_URL="${STATUS_URL:-https://localhost:8443/status}"
EXPECTED_CONTAINERS=(tts-launcher tts-launcher-proxy)

log "update-production.sh version ${VERSION}"

require_command git
require_command docker
require_command curl

[[ -f "$COMPOSE_FILE" ]] || fail "Missing compose file: $COMPOSE_FILE"
[[ -f "$COMPOSE_ENV_FILE" ]] || fail "Missing compose env file: $COMPOSE_ENV_FILE"

cd "$REPO_DIR"

if ! git diff --quiet || ! git diff --cached --quiet; then
  fail "Git working tree is not clean. Commit, stash, or discard local changes before running this script."
fi

log "Fetching latest changes from origin"
git fetch origin "$TARGET_BRANCH"

log "Checking out branch $TARGET_BRANCH"
git checkout "$TARGET_BRANCH"

log "Pulling latest fast-forward changes"
git pull --ff-only origin "$TARGET_BRANCH"

# Safely read a single key from the .env file without sourcing it as a shell
# script (sourcing causes unquoted multi-word values like BOT_COMMAND to be
# executed as commands).
_env_get() {
  local key="$1"
  grep -E "^${key}=" "$COMPOSE_ENV_FILE" | head -1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]$//"
}

BOT_IMAGE="$(_env_get BOT_IMAGE)"
BOT_IMAGE="${BOT_IMAGE:-acrttsconductorprod.azurecr.io/tts-conductor:latest}"
JOB_LAUNCHER_SHARED_SECRET="$(_env_get JOB_LAUNCHER_SHARED_SECRET)"

log "Pulling latest bot image: $BOT_IMAGE"
docker pull "$BOT_IMAGE"

log "Pulling compose-managed images"
"${COMPOSE_CMD[@]}" --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" pull

log "Rebuilding compose services"
"${COMPOSE_CMD[@]}" --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" build --pull

log "Restarting compose stack"
"${COMPOSE_CMD[@]}" --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans

for container_name in "${EXPECTED_CONTAINERS[@]}"; do
  status="$(docker inspect -f '{{.State.Status}}' "$container_name" 2>/dev/null || true)"
  if [[ "$status" != "running" ]]; then
    fail "Container $container_name is not running (status: ${status:-missing})"
  fi
  log "Container $container_name is running"
done

log "Checking launcher health endpoint: $HEALTHCHECK_URL"
health_payload="$(curl --silent --show-error --fail --max-time 20 --insecure "$HEALTHCHECK_URL")"
if [[ "$health_payload" != *'"ok":true'* ]]; then
  fail "Launcher health check did not report ok=true. Response: $health_payload"
fi
log "Launcher health check passed: $health_payload"

if [[ -n "${JOB_LAUNCHER_SHARED_SECRET:-}" ]]; then
  log "Checking launcher status endpoint"
  status_payload="$(curl --silent --show-error --fail --max-time 20 --insecure \
    -H "x-job-launcher-secret: ${JOB_LAUNCHER_SHARED_SECRET}" \
    "$STATUS_URL")"
  log "Launcher status response: $status_payload"
else
  log "Skipping launcher status endpoint check because JOB_LAUNCHER_SHARED_SECRET is not set"
fi

log "Production update complete"