#!/usr/bin/env bash
# SPDX-License-Identifier: CC-BY-NC-SA-4.0

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_ENV_FILE="${ROOT_ENV_FILE:-$REPO_DIR/.env}"
VM_ENV_FILE="${VM_ENV_FILE:-$SCRIPT_DIR/.env}"
AZURE_AUTH_ENV_FILE="${AZURE_AUTH_ENV_FILE:-/etc/tts-conductor/update-production.env}"
LAUNCH_URL="${LAUNCH_URL:-https://localhost:8443/launch}"
STATUS_URL="${STATUS_URL:-https://localhost:8443/status}"
BOT_CONTAINER_NAME="${BOT_CONTAINER_NAME:-tts-conductor-bot}"
WAIT_SECONDS="${WAIT_SECONDS:-30}"
SKIP_AZURE_LOGIN="${SKIP_AZURE_LOGIN:-false}"

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

compose_up() {
  if docker compose version >/dev/null 2>&1; then
    docker compose up -d --force-recreate tts-launcher tts-launcher-proxy
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose up -d --force-recreate tts-launcher tts-launcher-proxy
  else
    fail "Neither 'docker compose' nor 'docker-compose' is available"
  fi
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

print_usage() {
  cat <<'EOF'
Usage: ./refresh-bot-token.sh

Refreshes production bot runtime so it uses the latest DAILY_TOKEN in ../.env.

Actions performed:
1) Compute expected DAILY_TOKEN hash from repository .env
2) Login to Azure + ACR (unless SKIP_AZURE_LOGIN=true)
3) Recreate launcher containers and remove existing bot container
4) Trigger launch endpoint and wait for bot container to start
5) Compare expected token hash to in-container DAILY_TOKEN hash

Environment overrides:
- ROOT_ENV_FILE
- VM_ENV_FILE
- AZURE_AUTH_ENV_FILE
- LAUNCH_URL
- STATUS_URL
- BOT_CONTAINER_NAME
- WAIT_SECONDS
- SKIP_AZURE_LOGIN=true to skip az login/acr login
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  print_usage
  exit 0
fi

require_command docker
require_command curl
require_command sha256sum
require_command awk
require_command grep
require_command sed

[[ -f "$ROOT_ENV_FILE" ]] || fail "Missing runtime env file: $ROOT_ENV_FILE"

expected_token="$(env_get_from_file "$ROOT_ENV_FILE" DAILY_TOKEN)"
[[ -n "$expected_token" ]] || fail "DAILY_TOKEN is missing in $ROOT_ENV_FILE"

expected_hash="$(printf '%s' "$expected_token" | sha256sum | awk '{print $1}')"
log "Expected DAILY_TOKEN length: ${#expected_token}"
log "Expected DAILY_TOKEN sha256: $expected_hash"

bot_image="$(env_get_from_file "$VM_ENV_FILE" BOT_IMAGE)"
bot_image="${bot_image:-acrttsconductorprod.azurecr.io/tts-conductor:latest}"

if [[ "$SKIP_AZURE_LOGIN" != "true" ]]; then
  require_command az
  [[ -f "$AZURE_AUTH_ENV_FILE" ]] || fail "Missing Azure auth file: $AZURE_AUTH_ENV_FILE"

  # shellcheck disable=SC1090
  source "$AZURE_AUTH_ENV_FILE"
  [[ -n "${AZURE_CLIENT_ID:-}" ]] || fail "AZURE_CLIENT_ID missing in $AZURE_AUTH_ENV_FILE"
  [[ -n "${AZURE_CLIENT_SECRET:-}" ]] || fail "AZURE_CLIENT_SECRET missing in $AZURE_AUTH_ENV_FILE"
  [[ -n "${AZURE_TENANT_ID:-}" ]] || fail "AZURE_TENANT_ID missing in $AZURE_AUTH_ENV_FILE"

  log "Logging into Azure as service principal"
  az login --service-principal --username "$AZURE_CLIENT_ID" --password "$AZURE_CLIENT_SECRET" --tenant "$AZURE_TENANT_ID" >/dev/null

  if acr_name="$(acr_registry_name_from_image "$bot_image")"; then
    log "Logging into ACR: $acr_name"
    az acr login --name "$acr_name" >/dev/null
  else
    log "Skipping ACR login because BOT_IMAGE is not an Azure Container Registry image"
  fi
else
  log "Skipping Azure/ACR login because SKIP_AZURE_LOGIN=true"
fi

cd "$SCRIPT_DIR"

log "Recreating launcher containers"
compose_up

log "Removing existing bot container if present"
docker rm -f "$BOT_CONTAINER_NAME" >/dev/null 2>&1 || true

launcher_secret="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' tts-launcher | awk -F= '/^LAUNCHER_SHARED_SECRET=/{print substr($0,index($0,$2)); exit}')"
[[ -n "$launcher_secret" ]] || fail "Could not read LAUNCHER_SHARED_SECRET from tts-launcher container"

log "Calling launcher start endpoint"
launch_payload="$(curl -sk -X POST -H "x-job-launcher-secret: $launcher_secret" "$LAUNCH_URL")"
log "Launch response: $launch_payload"

log "Waiting for bot container to start"
for _ in $(seq 1 "$WAIT_SECONDS"); do
  if docker ps --format '{{.Names}}' | grep -qx "$BOT_CONTAINER_NAME"; then
    log "Bot container is running"
    break
  fi
  sleep 1
done

docker ps --format '{{.Names}}' | grep -qx "$BOT_CONTAINER_NAME" || fail "Bot container did not start within ${WAIT_SECONDS}s"

status_payload="$(curl -sk -H "x-job-launcher-secret: $launcher_secret" "$STATUS_URL")"
log "Status response: $status_payload"

actual_hash="$(docker exec "$BOT_CONTAINER_NAME" sh -lc 'printf "%s" "${DAILY_TOKEN:-}" | sha256sum | awk "{print \$1}"')"
actual_length="$(docker exec "$BOT_CONTAINER_NAME" sh -lc 'token="${DAILY_TOKEN:-}"; echo ${#token}')"

log "Actual DAILY_TOKEN length in bot: $actual_length"
log "Actual DAILY_TOKEN sha256 in bot: $actual_hash"

if [[ "$actual_hash" != "$expected_hash" ]]; then
  fail "Token hash mismatch. Expected $expected_hash, got $actual_hash"
fi

log "Success: bot is using the rotated DAILY_TOKEN"
