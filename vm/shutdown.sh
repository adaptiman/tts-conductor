#!/usr/bin/env bash
# SPDX-License-Identifier: CC-BY-NC-SA-4.0
#
# Gracefully shuts down the bot container and all associated compose services.
#
# Usage:
#   ./shutdown.sh              # stop containers only
#   ./shutdown.sh --images     # stop containers AND aggressively remove all project images

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_DIR/.env}"

REMOVE_IMAGES=0
for arg in "$@"; do
  [[ "$arg" == "--images" ]] && REMOVE_IMAGES=1
done

cd "$SCRIPT_DIR"

echo "Stopping bot container (tts-conductor-bot)..."
docker rm -f tts-conductor-bot 2>/dev/null && echo "  Removed tts-conductor-bot." || echo "  tts-conductor-bot was not running."

if [[ "$REMOVE_IMAGES" == "1" ]]; then
  echo "Bringing down compose services and removing all built images..."
  docker compose down --rmi all
else
  echo "Bringing down compose services..."
  docker compose down
fi

if [[ "$REMOVE_IMAGES" == "1" ]]; then
  # Resolve BOT_IMAGE from env file or fall back to the default used in docker-compose.yml.
  BOT_IMAGE=""
  if [[ -f "$ENV_FILE" ]]; then
    BOT_IMAGE="$(grep -E '^BOT_IMAGE=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- | sed "s/^['\"]//;s/['\"]$//" || true)"
  fi
  BOT_IMAGE="${BOT_IMAGE:-tts-conductor:local}"

  echo "Removing bot image (${BOT_IMAGE})..."
  docker rmi -f "$BOT_IMAGE" 2>/dev/null && echo "  Removed ${BOT_IMAGE}." || echo "  ${BOT_IMAGE} not found."

  echo "Pruning dangling images..."
  docker image prune --force

  echo "Docker disk usage after cleanup:"
  docker system df
fi

echo "Shutdown complete."
