#!/usr/bin/env bash
# SPDX-License-Identifier: CC-BY-NC-SA-4.0

set -Eeuo pipefail

LOG_DIR="/etc/tts-conductor"
LOG_FILE="${LOG_DIR}/docker-maintenance.log"

# Conservative defaults keep frequently used images and build cache so
# launcher-triggered rebuilds are fast and avoid repeated dependency downloads.
PRUNE_STOPPED_CONTAINERS_UNTIL="${PRUNE_STOPPED_CONTAINERS_UNTIL:-168h}"
PRUNE_DANGLING_IMAGES_ONLY="${PRUNE_DANGLING_IMAGES_ONLY:-1}"
PRUNE_UNUSED_VOLUMES="${PRUNE_UNUSED_VOLUMES:-0}"
PRUNE_BUILDER_CACHE="${PRUNE_BUILDER_CACHE:-0}"
PRUNE_BUILDER_CACHE_UNTIL="${PRUNE_BUILDER_CACHE_UNTIL:-720h}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG_FILE"
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

prepare_log_file() {
  mkdir -p "$LOG_DIR" || {
    echo "ERROR: Unable to create log directory: $LOG_DIR" >&2
    exit 1
  }

  touch "$LOG_FILE" || {
    echo "ERROR: Unable to create log file: $LOG_FILE" >&2
    exit 1
  }
}

main() {
  prepare_log_file

  require_command docker

  log "Docker maintenance start"

  # Capture a concise before snapshot for troubleshooting and trend checks.
  before_df="$(docker system df 2>&1)"
  log "Docker disk usage before prune:"
  while IFS= read -r line; do
    log "  $line"
  done <<<"$before_df"

  # 1) Remove stale stopped containers.
  containers_prune_output="$(docker container prune --force --filter "until=${PRUNE_STOPPED_CONTAINERS_UNTIL}" 2>&1)" || {
    log "Docker container prune command failed"
    while IFS= read -r line; do
      log "  $line"
    done <<<"$containers_prune_output"
    exit 1
  }

  log "Docker container prune output:"
  while IFS= read -r line; do
    log "  $line"
  done <<<"$containers_prune_output"

  # 2) Remove only dangling layers by default; keep tagged images like tts-conductor:local.
  if [[ "$PRUNE_DANGLING_IMAGES_ONLY" == "1" ]]; then
    images_prune_output="$(docker image prune --force 2>&1)" || {
      log "Docker dangling image prune command failed"
      while IFS= read -r line; do
        log "  $line"
      done <<<"$images_prune_output"
      exit 1
    }
    log "Docker dangling image prune output:"
    while IFS= read -r line; do
      log "  $line"
    done <<<"$images_prune_output"
  else
    log "Skipping image prune because PRUNE_DANGLING_IMAGES_ONLY=$PRUNE_DANGLING_IMAGES_ONLY"
  fi

  # 3) Remove unused networks (safe; recreated automatically by compose).
  network_prune_output="$(docker network prune --force 2>&1)" || {
    log "Docker network prune command failed"
    while IFS= read -r line; do
      log "  $line"
    done <<<"$network_prune_output"
    exit 1
  }
  log "Docker network prune output:"
  while IFS= read -r line; do
    log "  $line"
  done <<<"$network_prune_output"

  # 4) Optional: remove unused volumes (off by default because this can be destructive).
  if [[ "$PRUNE_UNUSED_VOLUMES" == "1" ]]; then
    volumes_prune_output="$(docker volume prune --force 2>&1)" || {
      log "Docker volume prune command failed"
      while IFS= read -r line; do
        log "  $line"
      done <<<"$volumes_prune_output"
      exit 1
    }
    log "Docker volume prune output:"
    while IFS= read -r line; do
      log "  $line"
    done <<<"$volumes_prune_output"
  else
    log "Skipping volume prune because PRUNE_UNUSED_VOLUMES=$PRUNE_UNUSED_VOLUMES"
  fi

  # 5) Optional: prune old builder cache. Keep disabled by default to avoid
  # dependency re-downloads on the next image rebuild.
  if [[ "$PRUNE_BUILDER_CACHE" == "1" ]]; then
    builder_prune_output="$(docker builder prune --force --filter "until=${PRUNE_BUILDER_CACHE_UNTIL}" 2>&1)" || {
      log "Docker builder prune command failed"
      while IFS= read -r line; do
        log "  $line"
      done <<<"$builder_prune_output"
      exit 1
    }
    log "Docker builder prune output:"
    while IFS= read -r line; do
      log "  $line"
    done <<<"$builder_prune_output"
  else
    log "Skipping builder cache prune because PRUNE_BUILDER_CACHE=$PRUNE_BUILDER_CACHE"
  fi

  after_df="$(docker system df 2>&1)"
  log "Docker disk usage after prune:"
  while IFS= read -r line; do
    log "  $line"
  done <<<"$after_df"

  log "Docker maintenance complete. Review before/after snapshots above for reclaimed space details."
}

main "$@"
