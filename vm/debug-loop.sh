#!/usr/bin/env bash
# SPDX-License-Identifier: CC-BY-NC-SA-4.0

set -Eeuo pipefail

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

fail() {
  log "ERROR: $*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage:
  ./vm/debug-loop.sh [options]

Options:
  --branch <name>       Fetch/checkout/pull this branch before running.
  --mode <mode>         One of: auto | bot-only | launcher-only | no-build
                        Default: auto
  --log-minutes <n>     Minutes of bot logs to show at end. Default: 3
  --help                Show this help.

Notes:
  - This script is intended for fast production VM debugging loops.
  - Launch is performed directly against tts-launcher on the internal Docker
    network to avoid nginx/TLS issues while troubleshooting.
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

MODE="auto"
BRANCH=""
LOG_MINUTES="3"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --branch)
      [[ $# -ge 2 ]] || fail "--branch requires a value"
      BRANCH="$2"
      shift 2
      ;;
    --mode)
      [[ $# -ge 2 ]] || fail "--mode requires a value"
      MODE="$2"
      shift 2
      ;;
    --log-minutes)
      [[ $# -ge 2 ]] || fail "--log-minutes requires a value"
      LOG_MINUTES="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "Unknown argument: $1"
      ;;
  esac
done

case "$MODE" in
  auto|bot-only|launcher-only|no-build) ;;
  *) fail "Invalid --mode '$MODE' (expected auto|bot-only|launcher-only|no-build)" ;;
esac

if ! [[ "$LOG_MINUTES" =~ ^[0-9]+$ ]]; then
  fail "--log-minutes must be an integer"
fi

require_command git
require_command docker
require_command awk

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

[[ -f "$COMPOSE_FILE" ]] || fail "Missing compose file: $COMPOSE_FILE"
[[ -f "$COMPOSE_ENV_FILE" ]] || fail "Missing compose env file: $COMPOSE_ENV_FILE"

cd "$REPO_DIR"

old_head="$(git rev-parse HEAD)"
if [[ -n "$BRANCH" ]]; then
  log "Fetching and switching to branch: $BRANCH"
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
fi
new_head="$(git rev-parse HEAD)"

changed_files=""
if [[ "$old_head" != "$new_head" ]]; then
  changed_files+="$(git diff --name-only "$old_head" "$new_head")"$'\n'
fi
changed_files+="$(git diff --name-only HEAD || true)"$'\n'
changed_files+="$(git diff --name-only --cached || true)"$'\n'
changed_files="$(printf '%s\n' "$changed_files" | sed '/^$/d' | sort -u || true)"

log "Mode=$MODE"
if [[ -n "$BRANCH" ]]; then
  log "Branch=$(git rev-parse --abbrev-ref HEAD)"
fi
if [[ -n "$changed_files" ]]; then
  log "Changed files detected:" 
  printf '%s\n' "$changed_files"
else
  log "No changed files detected; running with current mode decisions."
fi

launcher_changed=false
bot_changed=false
config_changed=false

while IFS= read -r file; do
  [[ -n "$file" ]] || continue
  case "$file" in
    vm/launcher/*|vm/nginx/*|vm/docker-compose.yml|vm/.env)
      launcher_changed=true
      config_changed=true
      ;;
    .env)
      config_changed=true
      ;;
    Dockerfile|requirements.txt|pyproject.toml|*.py)
      # Launcher python changes are handled by launcher bucket above.
      if [[ "$file" != vm/launcher/* ]]; then
        bot_changed=true
      fi
      ;;
    *)
      ;;
  esac
done <<< "$changed_files"

do_build_launcher=false
do_recreate_launcher=false
do_build_bot=false

case "$MODE" in
  launcher-only)
    do_build_launcher=true
    do_recreate_launcher=true
    ;;
  bot-only)
    do_build_bot=true
    ;;
  no-build)
    ;;
  auto)
    if [[ "$launcher_changed" == true ]]; then
      do_build_launcher=true
      do_recreate_launcher=true
    elif [[ "$config_changed" == true ]]; then
      do_recreate_launcher=true
    fi

    if [[ "$bot_changed" == true ]]; then
      do_build_bot=true
    fi
    ;;
esac

if [[ "$do_build_launcher" == true ]]; then
  log "Building launcher image"
  BOT_PULL_ON_START=false "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --env-file "$COMPOSE_ENV_FILE" build tts-launcher
fi

if [[ "$do_recreate_launcher" == true ]]; then
  log "Recreating launcher/proxy containers"
  BOT_PULL_ON_START=false "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --env-file "$COMPOSE_ENV_FILE" up -d --force-recreate tts-launcher tts-launcher-proxy
else
  log "Ensuring launcher/proxy are running"
  BOT_PULL_ON_START=false "${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --env-file "$COMPOSE_ENV_FILE" up -d tts-launcher tts-launcher-proxy
fi

BOT_IMAGE="$(env_get_from_file "$COMPOSE_ENV_FILE" BOT_IMAGE)"
BOT_IMAGE="${BOT_IMAGE:-tts-conductor:local}"

if [[ "$do_build_bot" == true ]]; then
  log "Building bot image: $BOT_IMAGE"
  docker build -t "$BOT_IMAGE" "$REPO_DIR"
fi

secret="$(env_get_from_file "$COMPOSE_ENV_FILE" JOB_LAUNCHER_SHARED_SECRET)"
if [[ -z "$secret" ]]; then
  secret="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' tts-launcher | awk -F= '/^LAUNCHER_SHARED_SECRET=/{print substr($0,index($0,$2)); exit}')"
fi
[[ -n "$secret" ]] || fail "Missing launcher secret (JOB_LAUNCHER_SHARED_SECRET)"

log "Launching bot via internal launcher endpoint"
launch_output="$(docker run --rm --network tts-launcher-internal curlimages/curl:8.7.1 \
  -sS -w '\nHTTP_CODE=%{http_code}\n' \
  -X POST http://tts-launcher:8080/launch \
  -H "x-job-launcher-secret: $secret")"
printf '%s\n' "$launch_output"

http_code="$(printf '%s\n' "$launch_output" | awk -F= '/^HTTP_CODE=/{print $2}' | tail -1)"
if [[ "$http_code" != "200" ]]; then
  fail "Launch failed with HTTP_CODE=${http_code:-unknown}"
fi

log "Bot container status"
docker ps -a --filter name=tts-conductor-bot --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'

log "Filtered bot logs (last ${LOG_MINUTES}m)"
if ! docker logs --since "${LOG_MINUTES}m" tts-conductor-bot 2>&1 | grep -E '\[highlight\]|\[utterance\]|\[speak\]|instapaper|[Ee]rror'; then
  log "No filtered matches found; showing last 80 lines instead"
  docker logs --tail 80 tts-conductor-bot 2>&1 || true
fi

log "Debug loop completed"
