#!/usr/bin/env bash
# SPDX-License-Identifier: CC-BY-NC-SA-4.0

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-$SCRIPT_DIR/.env}"
BASE_URL="${BASE_URL:-https://localhost:8443}"
LAUNCHER_CONTAINER_NAME="${LAUNCHER_CONTAINER_NAME:-tts-launcher}"
WAIT_SECONDS="${WAIT_SECONDS:-30}"
CURL_TIMEOUT_SECONDS="${CURL_TIMEOUT_SECONDS:-8}"
INSECURE_TLS="${INSECURE_TLS:-true}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./vm/force-relaunch-bot.sh

Force-restarts the bot by calling launcher /stop and /launch.
This is useful when the bot container is running but not actually joined to Daily.

Environment overrides:
  COMPOSE_ENV_FILE        default: vm/.env (falls back to .env when missing)
  BASE_URL                default: https://localhost:8443
  LAUNCHER_CONTAINER_NAME default: tts-launcher
  WAIT_SECONDS            default: 30
  CURL_TIMEOUT_SECONDS    default: 8
  INSECURE_TLS            default: true
EOF
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

env_get_from_file() {
  local env_file="$1"
  local key="$2"

  [[ -f "$env_file" ]] || return 0
  grep -E "^${key}=" "$env_file" | head -1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]$//"
}

get_launcher_secret() {
  local secret

  secret="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$LAUNCHER_CONTAINER_NAME" 2>/dev/null | awk -F= '/^LAUNCHER_SHARED_SECRET=/{print substr($0,index($0,$2)); exit}')"
  if [[ -n "$secret" ]]; then
    printf '%s\n' "$secret"
    return 0
  fi

  secret="$(env_get_from_file "$COMPOSE_ENV_FILE" JOB_LAUNCHER_SHARED_SECRET)"
  if [[ -n "$secret" ]]; then
    printf '%s\n' "$secret"
    return 0
  fi

  return 1
}

call_endpoint() {
  local method="$1"
  local path="$2"
  local secret="$3"
  local insecure_flag=()

  if [[ "${INSECURE_TLS,,}" == "true" || "${INSECURE_TLS,,}" == "1" || "${INSECURE_TLS,,}" == "yes" || "${INSECURE_TLS,,}" == "on" ]]; then
    insecure_flag=(-k)
  fi

  curl -sS "${insecure_flag[@]}" \
    --connect-timeout "$CURL_TIMEOUT_SECONDS" \
    --max-time "$CURL_TIMEOUT_SECONDS" \
    -X "$method" \
    -H "x-job-launcher-secret: $secret" \
    "$BASE_URL$path" \
    -w '\nHTTP_CODE=%{http_code}\n'
}

extract_http_code() {
  awk -F= '/^HTTP_CODE=/{print $2}' | tail -1
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

require_command docker
require_command curl
require_command awk
require_command sed
require_command grep

if [[ ! -f "$COMPOSE_ENV_FILE" ]]; then
  COMPOSE_ENV_FILE="$REPO_DIR/.env"
fi

[[ -f "$COMPOSE_ENV_FILE" ]] || fail "Missing env file (checked vm/.env and .env)."

secret="$(get_launcher_secret || true)"
[[ -n "$secret" ]] || fail "Could not determine launcher secret from ${LAUNCHER_CONTAINER_NAME} or $COMPOSE_ENV_FILE"

bot_container_name="$(env_get_from_file "$COMPOSE_ENV_FILE" BOT_CONTAINER_NAME)"
bot_container_name="${bot_container_name:-tts-conductor-bot}"

log "Using launcher URL: $BASE_URL"
log "Using bot container: $bot_container_name"

log "Current launcher status"
status_before="$(call_endpoint GET /status "$secret" || true)"
printf '%s\n' "$status_before"

log "Stopping bot via launcher"
stop_output="$(call_endpoint POST /stop "$secret")"
stop_code="$(printf '%s\n' "$stop_output" | extract_http_code)"
printf '%s\n' "$stop_output"
[[ "$stop_code" == "200" ]] || fail "Launcher /stop failed with HTTP_CODE=${stop_code:-unknown}"

log "Launching bot via launcher"
launch_output="$(call_endpoint POST /launch "$secret")"
launch_code="$(printf '%s\n' "$launch_output" | extract_http_code)"
printf '%s\n' "$launch_output"
[[ "$launch_code" == "200" ]] || fail "Launcher /launch failed with HTTP_CODE=${launch_code:-unknown}"

log "Waiting up to ${WAIT_SECONDS}s for running bot container"
for _ in $(seq 1 "$WAIT_SECONDS"); do
  state="$(docker inspect -f '{{.State.Status}}' "$bot_container_name" 2>/dev/null || echo missing)"
  if [[ "$state" == "running" ]]; then
    log "Bot container is running"
    break
  fi
  sleep 1
done

state="$(docker inspect -f '{{.State.Status}}' "$bot_container_name" 2>/dev/null || echo missing)"
if [[ "$state" != "running" ]]; then
  fail "Bot did not reach running state (state=$state)"
fi

log "Final launcher status"
status_after="$(call_endpoint GET /status "$secret" || true)"
printf '%s\n' "$status_after"

log "Done"
