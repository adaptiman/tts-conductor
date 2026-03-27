#!/usr/bin/env bash
# SPDX-License-Identifier: CC-BY-NC-SA-4.0

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_ENV_FILE="${ROOT_ENV_FILE:-$REPO_DIR/.env}"
BASE_URL="${BASE_URL:-https://localhost:8443}"
LAUNCHER_CONTAINER="${LAUNCHER_CONTAINER:-tts-launcher}"
BOT_CONTAINER="${BOT_CONTAINER:-tts-conductor-bot}"
TAIL_LINES="${TAIL_LINES:-120}"
TRIGGER_LAUNCH=false

log_section() {
  printf '\n== %s ==\n' "$1"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "ERROR: Required command not found: $1" >&2
    exit 1
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

get_launcher_secret() {
  local secret

  secret="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$LAUNCHER_CONTAINER" 2>/dev/null | awk -F= '/^LAUNCHER_SHARED_SECRET=/{print substr($0,index($0,$2)); exit}')"
  if [[ -n "$secret" ]]; then
    printf '%s\n' "$secret"
    return 0
  fi

  secret="$(env_get_from_file "$ROOT_ENV_FILE" JOB_LAUNCHER_SHARED_SECRET)"
  if [[ -n "$secret" ]]; then
    printf '%s\n' "$secret"
    return 0
  fi

  return 1
}

show_compose_ps() {
  if docker compose version >/dev/null 2>&1; then
    docker compose ps
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose ps
  else
    echo "docker compose not available"
  fi
}

show_env_presence() {
  local daily_room_url daily_token daily_api_key

  daily_room_url="$(env_get_from_file "$ROOT_ENV_FILE" DAILY_ROOM_URL)"
  daily_token="$(env_get_from_file "$ROOT_ENV_FILE" DAILY_TOKEN)"
  daily_api_key="$(env_get_from_file "$ROOT_ENV_FILE" DAILY_API_KEY)"

  printf 'ROOT_ENV_FILE=%s\n' "$ROOT_ENV_FILE"
  printf 'DAILY_ROOM_URL=%s\n' "$( [[ -n "$daily_room_url" ]] && echo present || echo missing )"
  printf 'DAILY_TOKEN=%s' "$( [[ -n "$daily_token" ]] && echo present || echo missing )"
  if [[ -n "$daily_token" ]]; then
    printf ' (length=%s)' "${#daily_token}"
  fi
  printf '\n'
  printf 'DAILY_API_KEY=%s' "$( [[ -n "$daily_api_key" ]] && echo present || echo missing )"
  if [[ -n "$daily_api_key" ]]; then
    printf ' (length=%s)' "${#daily_api_key}"
  fi
  printf '\n'
}

show_launcher_status() {
  local secret="$1"

  printf 'HEALTH: '
  curl -sk -H "x-job-launcher-secret: $secret" "$BASE_URL/health"
  printf '\n'

  printf 'STATUS: '
  curl -sk -H "x-job-launcher-secret: $secret" "$BASE_URL/status"
  printf '\n'
}

trigger_launch() {
  local secret="$1"

  printf 'LAUNCH: '
  curl -sk -X POST -H "x-job-launcher-secret: $secret" "$BASE_URL/launch"
  printf '\n'
}

show_bot_state() {
  docker ps -a --filter "name=^${BOT_CONTAINER}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

  if docker ps -a --format '{{.Names}}' | grep -qx "$BOT_CONTAINER"; then
    printf 'BOT_INSPECT: '
    docker inspect -f 'status={{.State.Status}} exitCode={{.State.ExitCode}} error={{json .State.Error}} startedAt={{.State.StartedAt}} finishedAt={{.State.FinishedAt}}' "$BOT_CONTAINER"
  fi
}

show_logs() {
  log_section "launcher logs (last ${TAIL_LINES})"
  docker logs --tail "$TAIL_LINES" "$LAUNCHER_CONTAINER" 2>&1 || true

  if docker ps -a --format '{{.Names}}' | grep -qx "$BOT_CONTAINER"; then
    log_section "bot logs (last ${TAIL_LINES})"
    docker logs --tail "$TAIL_LINES" "$BOT_CONTAINER" 2>&1 || true
  fi
}

usage() {
  cat <<'EOF'
Usage: ./diagnose-bot-launch.sh [--trigger-launch]

Checks launcher health, bot container state, relevant env presence, and logs.

Options:
  --trigger-launch   Also call the launch endpoint and re-check bot state.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --trigger-launch)
      TRIGGER_LAUNCH=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_command docker
require_command curl
require_command awk
require_command sed
require_command grep

secret="$(get_launcher_secret)" || {
  echo "ERROR: Could not determine launcher secret from container or $ROOT_ENV_FILE" >&2
  exit 1
}

log_section "repo and compose state"
printf 'PWD=%s\n' "$REPO_DIR"
if git -C "$REPO_DIR" rev-parse --short HEAD >/dev/null 2>&1; then
  printf 'GIT_HEAD=%s\n' "$(git -C "$REPO_DIR" rev-parse --short HEAD)"
fi
show_compose_ps || true

log_section "env presence"
show_env_presence

log_section "launcher health and status"
show_launcher_status "$secret"

log_section "bot container state"
show_bot_state

if [[ "$TRIGGER_LAUNCH" == "true" ]]; then
  log_section "manual launch"
  trigger_launch "$secret"
  sleep 3

  log_section "post-launch status"
  show_launcher_status "$secret"

  log_section "post-launch bot state"
  show_bot_state
fi

show_logs