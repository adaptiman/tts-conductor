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

env_get_from_file() {
  local env_file="$1"
  local key="$2"

  [[ -f "$env_file" ]] || return 0
  grep -E "^${key}=" "$env_file" | head -1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]$//"
}

if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose)
else
  fail "Neither 'docker compose' nor 'docker-compose' is available"
fi

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

require_command curl
require_command python3
require_command docker

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

detect_repo_dir() {
  local candidates=()
  local candidate

  # Preferred: git root of current working directory.
  if command -v git >/dev/null 2>&1; then
    candidate="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$candidate" ]]; then
      candidates+=("$candidate")
    fi
  fi

  # Script location candidates (supports running from repo/vm or copied script).
  candidates+=("$SCRIPT_DIR/..")
  candidates+=("$SCRIPT_DIR")
  candidates+=("$PWD")

  for candidate in "${candidates[@]}"; do
    candidate="$(cd "$candidate" 2>/dev/null && pwd || true)"
    [[ -n "$candidate" ]] || continue
    if [[ -f "$candidate/vm/docker-compose.yml" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

REPO_DIR="$(detect_repo_dir || true)"
[[ -n "$REPO_DIR" ]] || fail "Could not locate repo root containing vm/docker-compose.yml. Run from inside the repository or move the script back to repo/vm/."

COMPOSE_FILE="$REPO_DIR/vm/docker-compose.yml"
VM_DIR="$REPO_DIR/vm"
COMPOSE_ENV_FILE="$VM_DIR/.env"

# Some deployments keep compose vars in vm/.env while bot runtime vars are in
# repo-root .env mounted via BOT_ENV_SOURCE (default: ../.env from vm/).
if [[ ! -f "$COMPOSE_ENV_FILE" ]]; then
  COMPOSE_ENV_FILE="$REPO_DIR/.env"
fi

[[ -f "$COMPOSE_ENV_FILE" ]] || fail "Missing compose env file (checked vm/.env and .env)"

BOT_ENV_SOURCE_RAW="$(env_get_from_file "$COMPOSE_ENV_FILE" BOT_ENV_SOURCE)"
BOT_ENV_SOURCE_RAW="${BOT_ENV_SOURCE_RAW:-../.env}"

if [[ "$BOT_ENV_SOURCE_RAW" == /* ]]; then
  BOT_ENV_FILE="$BOT_ENV_SOURCE_RAW"
else
  BOT_ENV_FILE="$(cd "$VM_DIR" && cd "$(dirname "$BOT_ENV_SOURCE_RAW")" && pwd)/$(basename "$BOT_ENV_SOURCE_RAW")"
fi

[[ -f "$BOT_ENV_FILE" ]] || fail "Missing bot env file resolved from BOT_ENV_SOURCE=${BOT_ENV_SOURCE_RAW}: $BOT_ENV_FILE"

ROOM_NAME="${1:-instabot}"
EXPIRY_HOURS="${2:-12}"
if ! [[ "$EXPIRY_HOURS" =~ ^[0-9]+$ ]]; then
  fail "Expiry hours must be an integer; got '$EXPIRY_HOURS'"
fi

DAILY_API_KEY="${DAILY_API_KEY:-$(env_get_from_file "$BOT_ENV_FILE" DAILY_API_KEY)}"
JOB_LAUNCHER_SHARED_SECRET="${JOB_LAUNCHER_SHARED_SECRET:-$(env_get_from_file "$COMPOSE_ENV_FILE" JOB_LAUNCHER_SHARED_SECRET)}"

[[ -n "$DAILY_API_KEY" ]] || fail "DAILY_API_KEY is missing (env or bot env file: $BOT_ENV_FILE)"
[[ -n "$JOB_LAUNCHER_SHARED_SECRET" ]] || fail "JOB_LAUNCHER_SHARED_SECRET is missing (env or compose env file: $COMPOSE_ENV_FILE)"

log "Using compose env file: $COMPOSE_ENV_FILE"
log "Using bot env file: $BOT_ENV_FILE"
log "Using DAILY_API_KEY length: ${#DAILY_API_KEY}"

EXP_TS="$(( $(date +%s) + (EXPIRY_HOURS * 3600) ))"

log "Generating Daily meeting token for room '$ROOM_NAME' (expiry ${EXPIRY_HOURS}h)"
API_RESPONSE="$({
  curl -sS -X POST "https://api.daily.co/v1/meeting-tokens" \
    -H "Authorization: Bearer ${DAILY_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"properties\":{\"room_name\":\"${ROOM_NAME}\",\"exp\":${EXP_TS}}}" \
    -w "\n%{http_code}";
} || true)"

HTTP_CODE="${API_RESPONSE##*$'\n'}"
API_BODY="${API_RESPONSE%$'\n'*}"

if [[ -z "$HTTP_CODE" || ! "$HTTP_CODE" =~ ^[0-9]{3}$ ]]; then
  fail "Daily token API returned an unexpected response (no HTTP code). Raw response: ${API_RESPONSE}"
fi

if [[ "$HTTP_CODE" != "200" ]]; then
  fail "Daily token API failed with HTTP ${HTTP_CODE}. Response: ${API_BODY}"
fi

NEW_TOKEN="$(printf '%s' "$API_BODY" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token", ""))')"

[[ -n "$NEW_TOKEN" ]] || fail "Failed to generate token"
log "Generated token length: ${#NEW_TOKEN}"

BACKUP_FILE="${BOT_ENV_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
cp "$BOT_ENV_FILE" "$BACKUP_FILE"
log "Backed up env file to $BACKUP_FILE"

BACKUP_RETENTION_COUNT="${BACKUP_RETENTION_COUNT:-7}"
if [[ "$BACKUP_RETENTION_COUNT" =~ ^[0-9]+$ ]] && (( BACKUP_RETENTION_COUNT > 0 )); then
  mapfile -t backup_files < <(find "$(dirname "$BOT_ENV_FILE")" -maxdepth 1 -type f -name "$(basename "$BOT_ENV_FILE").bak.*" -printf '%T@ %p\n' | sort -nr | awk '{print $2}')
  if (( ${#backup_files[@]} > BACKUP_RETENTION_COUNT )); then
    for old_backup in "${backup_files[@]:BACKUP_RETENTION_COUNT}"; do
      rm -f "$old_backup"
      log "Pruned old backup: $old_backup"
    done
  fi
fi

if grep -q '^DAILY_TOKEN=' "$BOT_ENV_FILE"; then
  sed -i "s|^DAILY_TOKEN=.*$|DAILY_TOKEN=${NEW_TOKEN}|" "$BOT_ENV_FILE"
else
  printf '\nDAILY_TOKEN=%s\n' "$NEW_TOKEN" >> "$BOT_ENV_FILE"
fi
log "Updated DAILY_TOKEN in $BOT_ENV_FILE"

log "Recreating launcher stack"
"${COMPOSE_CMD[@]}" -f "$COMPOSE_FILE" --env-file "$COMPOSE_ENV_FILE" up -d --force-recreate

log "Waiting for launcher health"
for _ in $(seq 1 30); do
  if curl -sk "https://localhost:8443/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

log "Removing existing bot container directly to avoid stale-env reuse"
docker rm -f tts-conductor-bot >/dev/null 2>&1 || true

log "Launching bot container via launcher"
launch_ok=""
for _ in $(seq 1 20); do
  launch_payload="$(curl -sk -X POST "https://localhost:8443/launch" \
    -H "x-job-launcher-secret: ${JOB_LAUNCHER_SHARED_SECRET}" || true)"
  if printf '%s' "$launch_payload" | grep -q '"ok":true'; then
    launch_ok="$launch_payload"
    break
  fi
  sleep 1
done

[[ -n "$launch_ok" ]] || fail "Launcher /launch did not return ok=true"
log "Launch response: $launch_ok"

log "Verifying token inside running bot container"
docker exec tts-conductor-bot /bin/sh -lc 'python - <<PY
import os
import base64
import json
import time

t = os.getenv("DAILY_TOKEN", "")
if not t:
    print("NO_TOKEN")
    raise SystemExit(1)
parts = t.split(".")
if len(parts) != 3:
    print("NOT_JWT")
    raise SystemExit(1)
p = parts[1] + "=" * (-len(parts[1]) % 4)
claims = json.loads(base64.urlsafe_b64decode(p.encode()))
exp = int(claims.get("exp", 0) or 0)
now = int(time.time())
print("exp:", exp)
print("now:", now)
print("expired:", now > exp)
print("room:", claims.get("r") or claims.get("room"))
PY'

log "Recent bot logs (last 2 minutes)"
docker logs --since 2m tts-conductor-bot | egrep -i "daily|join|room|token|unauthor|forbidden|error" || true

log "Done"
