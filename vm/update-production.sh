#!/usr/bin/env bash

set -Eeuo pipefail

VERSION="1.2.0"

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

env_get_from_file() {
  local env_file="$1"
  local key="$2"

  if [[ ! -f "$env_file" ]]; then
    return 0
  fi

  grep -E "^${key}=" "$env_file" | head -1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]$//"
}

docker_image_id() {
  local image_ref="$1"
  docker image inspect "$image_ref" --format '{{.Id}}' 2>/dev/null || true
}

launcher_get() {
  local endpoint="$1"
  curl --silent --show-error --fail --max-time 20 --insecure \
    -H "x-job-launcher-secret: ${JOB_LAUNCHER_SHARED_SECRET}" \
    "$endpoint"
}

launcher_post() {
  local endpoint="$1"
  curl --silent --show-error --fail --max-time 20 --insecure \
    -X POST \
    -H "x-job-launcher-secret: ${JOB_LAUNCHER_SHARED_SECRET}" \
    "$endpoint"
}

acr_registry_name_from_image() {
  local image_ref="$1"
  local registry_host

  registry_host="${image_ref%%/*}"
  if [[ "$registry_host" != *.* ]]; then
    return 1
  fi

  if [[ "$registry_host" == *.azurecr.io ]]; then
    printf '%s\n' "${registry_host%%.azurecr.io}"
    return 0
  fi

  return 1
}

azure_login_service_principal() {
  local client_id="$1"
  local client_secret="$2"
  local tenant_id="$3"

  if [[ -z "$client_id" || -z "$client_secret" || -z "$tenant_id" ]]; then
    fail "Missing Azure service principal credentials. Set AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, and AZURE_TENANT_ID in the environment or in ${AZURE_AUTH_ENV_FILE}."
  fi

  log "Logging into Azure with service principal ${client_id}"
  az login \
    --service-principal \
    --username "$client_id" \
    --password "$client_secret" \
    --tenant "$tenant_id" \
    >/dev/null
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
AZURE_AUTH_ENV_FILE="${AZURE_AUTH_ENV_FILE:-/etc/tts-conductor/update-production.env}"
TARGET_BRANCH="${1:-main}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-https://localhost:8443/health}"
STATUS_URL="${STATUS_URL:-https://localhost:8443/status}"
LAUNCH_URL="${LAUNCH_URL:-https://localhost:8443/launch}"
STOP_URL="${STOP_URL:-https://localhost:8443/stop}"
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

PRE_PULL_HEAD="$(git rev-parse HEAD)"

log "Fetching latest changes from origin"
git fetch origin "$TARGET_BRANCH"

log "Checking out branch $TARGET_BRANCH"
git checkout "$TARGET_BRANCH"

log "Pulling latest fast-forward changes"
git pull --ff-only origin "$TARGET_BRANCH"

POST_PULL_HEAD="$(git rev-parse HEAD)"
REPO_CHANGED=0
if [[ "$PRE_PULL_HEAD" != "$POST_PULL_HEAD" ]]; then
  REPO_CHANGED=1
  log "Repository updated: ${PRE_PULL_HEAD:0:12} -> ${POST_PULL_HEAD:0:12}"
else
  log "No repository update detected"
fi

# Safely read a single key from env files without sourcing them as shell scripts
# (sourcing causes unquoted multi-word values like BOT_COMMAND to be executed as
# commands).
BOT_IMAGE="$(env_get_from_file "$COMPOSE_ENV_FILE" BOT_IMAGE)"
BOT_IMAGE="${BOT_IMAGE:-acrttsconductorprod.azurecr.io/tts-conductor:latest}"
JOB_LAUNCHER_SHARED_SECRET="$(env_get_from_file "$COMPOSE_ENV_FILE" JOB_LAUNCHER_SHARED_SECRET)"
AZURE_CLIENT_ID="${AZURE_CLIENT_ID:-$(env_get_from_file "$AZURE_AUTH_ENV_FILE" AZURE_CLIENT_ID)}"
AZURE_CLIENT_SECRET="${AZURE_CLIENT_SECRET:-$(env_get_from_file "$AZURE_AUTH_ENV_FILE" AZURE_CLIENT_SECRET)}"
AZURE_TENANT_ID="${AZURE_TENANT_ID:-$(env_get_from_file "$AZURE_AUTH_ENV_FILE" AZURE_TENANT_ID)}"

PRE_PULL_BOT_IMAGE_ID="$(docker_image_id "$BOT_IMAGE")"

if ACR_NAME="$(acr_registry_name_from_image "$BOT_IMAGE")"; then
  require_command az
  azure_login_service_principal "$AZURE_CLIENT_ID" "$AZURE_CLIENT_SECRET" "$AZURE_TENANT_ID"
  log "Logging into Azure Container Registry: $ACR_NAME"
  az acr login --name "$ACR_NAME" >/dev/null
fi

log "Pulling latest bot image: $BOT_IMAGE"
docker pull "$BOT_IMAGE"

POST_PULL_BOT_IMAGE_ID="$(docker_image_id "$BOT_IMAGE")"
BOT_IMAGE_CHANGED=0
if [[ -n "$POST_PULL_BOT_IMAGE_ID" && "$PRE_PULL_BOT_IMAGE_ID" != "$POST_PULL_BOT_IMAGE_ID" ]]; then
  BOT_IMAGE_CHANGED=1
  if [[ -n "$PRE_PULL_BOT_IMAGE_ID" ]]; then
    log "Bot image updated: ${PRE_PULL_BOT_IMAGE_ID:0:19} -> ${POST_PULL_BOT_IMAGE_ID:0:19}"
  else
    log "Bot image downloaded locally: ${POST_PULL_BOT_IMAGE_ID:0:19}"
  fi
else
  log "No bot image update detected"
fi

if [[ "$REPO_CHANGED" -eq 0 && "$BOT_IMAGE_CHANGED" -eq 0 ]]; then
  log "No repository or bot image update detected; skipping rebuild and restart"
  exit 0
fi

if [[ "$REPO_CHANGED" -eq 1 ]]; then
  log "Pulling compose-managed images"
  "${COMPOSE_CMD[@]}" --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" pull

  log "Rebuilding compose services"
  "${COMPOSE_CMD[@]}" --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" build --pull

  log "Restarting compose stack"
  "${COMPOSE_CMD[@]}" --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_FILE" up -d --remove-orphans
else
  log "Skipping compose rebuild because repository did not change"
fi

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
  status_payload="$(launcher_get "$STATUS_URL")"

  if [[ "$BOT_IMAGE_CHANGED" -eq 1 && "$status_payload" == *'"running":true'* ]]; then
    log "Restarting active bot container so it uses the updated image"
    stop_payload="$(launcher_post "$STOP_URL")"
    log "Launcher stop response: $stop_payload"
    launch_payload="$(launcher_post "$LAUNCH_URL")"
    log "Launcher launch response: $launch_payload"
    status_payload="$(launcher_get "$STATUS_URL")"
  elif [[ "$BOT_IMAGE_CHANGED" -eq 1 ]]; then
    log "Bot image updated, but no active bot container is running; future launches will use the new image"
  fi

  log "Launcher status response: $status_payload"
else
  log "Skipping launcher status endpoint check because JOB_LAUNCHER_SHARED_SECRET is not set"
fi

log "Production update complete"