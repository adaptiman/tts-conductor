#!/usr/bin/env bash
# SPDX-License-Identifier: CC-BY-NC-SA-4.0

set -Eeuo pipefail

BOT_CONTAINER_NAME="${BOT_CONTAINER_NAME:-tts-conductor-bot}"
LAUNCHER_CONTAINER_NAME="${LAUNCHER_CONTAINER_NAME:-tts-launcher}"
STATUS_URL="${STATUS_URL:-https://localhost:8443/status}"
CURL_TIMEOUT_SECONDS="${CURL_TIMEOUT_SECONDS:-5}"
SHOW_STATUS="${SHOW_STATUS:-true}"

print_usage() {
  cat <<'EOF'
Usage: ./vm/is-bot-running.sh [--no-status]

Checks whether the bot container is currently running.

Exit codes:
  0  Bot is running
  1  Bot is not running (missing/exited)
  2  Required dependency missing

Environment overrides:
  BOT_CONTAINER_NAME      (default: tts-conductor-bot)
  LAUNCHER_CONTAINER_NAME (default: tts-launcher)
  STATUS_URL              (default: https://localhost:8443/status)
  CURL_TIMEOUT_SECONDS    (default: 5)
  SHOW_STATUS             (default: true)
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  print_usage
  exit 0
fi

if [[ "${1:-}" == "--no-status" ]]; then
  SHOW_STATUS="false"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker is required" >&2
  exit 2
fi

state="$(docker inspect -f '{{.State.Status}}' "$BOT_CONTAINER_NAME" 2>/dev/null || echo "missing")"

if [[ "$state" == "running" ]]; then
  echo "BOT_RUNNING=true"
  echo "BOT_CONTAINER_NAME=$BOT_CONTAINER_NAME"
  echo "BOT_STATE=$state"
  running_exit=0
else
  echo "BOT_RUNNING=false"
  echo "BOT_CONTAINER_NAME=$BOT_CONTAINER_NAME"
  echo "BOT_STATE=$state"
  running_exit=1
fi

if [[ "$SHOW_STATUS" == "true" ]] && command -v curl >/dev/null 2>&1; then
  launcher_secret="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$LAUNCHER_CONTAINER_NAME" 2>/dev/null | awk -F= '/^LAUNCHER_SHARED_SECRET=/{print substr($0,index($0,$2)); exit}')"

  if [[ -n "$launcher_secret" ]]; then
    status_payload="$(curl -sk --max-time "$CURL_TIMEOUT_SECONDS" -H "x-job-launcher-secret: $launcher_secret" "$STATUS_URL" || true)"
    if [[ -n "$status_payload" ]]; then
      echo "LAUNCHER_STATUS_PAYLOAD=$status_payload"
    else
      echo "LAUNCHER_STATUS_PAYLOAD=unavailable"
    fi
  else
    echo "LAUNCHER_STATUS_PAYLOAD=unavailable"
  fi
fi

exit "$running_exit"
