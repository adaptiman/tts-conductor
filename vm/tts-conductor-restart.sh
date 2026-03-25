#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: Missing env file: $ENV_FILE" >&2
  exit 1
fi

if [[ -z "${JOB_LAUNCHER_SHARED_SECRET:-}" ]]; then
  JOB_LAUNCHER_SHARED_SECRET="$(grep -E '^JOB_LAUNCHER_SHARED_SECRET=' "$ENV_FILE" | head -1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]$//")"
fi

if [[ -z "${JOB_LAUNCHER_SHARED_SECRET:-}" ]]; then
  echo "ERROR: JOB_LAUNCHER_SHARED_SECRET is not set in env or $ENV_FILE" >&2
  exit 1
fi

cd "$SCRIPT_DIR"

docker compose up -d --force-recreate tts-launcher tts-launcher-proxy
docker rm -f tts-conductor-bot 2>/dev/null || true
curl -sS -X POST https://cookbook.thesweeneys.org:8443/launch \
  -H "x-job-launcher-secret: ${JOB_LAUNCHER_SHARED_SECRET}"

docker compose ps
docker ps --filter name=tts-conductor-bot
