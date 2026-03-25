#!/usr/bin/env bash
# SPDX-License-Identifier: CC-BY-NC-SA-4.0

set -Eeuo pipefail

LOG_DIR="/etc/tts-conductor"
LOG_FILE="${LOG_DIR}/docker-maintenance.log"

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

  prune_output="$(docker system prune --all --volumes --force 2>&1)" || {
    log "Docker prune command failed"
    while IFS= read -r line; do
      log "  $line"
    done <<<"$prune_output"
    exit 1
  }

  reclaimed="$(printf '%s\n' "$prune_output" | sed -n 's/^Total reclaimed space: //p' | tail -1)"
  reclaimed="${reclaimed:-0B}"

  log "Docker prune output:"
  while IFS= read -r line; do
    log "  $line"
  done <<<"$prune_output"

  after_df="$(docker system df 2>&1)"
  log "Docker disk usage after prune:"
  while IFS= read -r line; do
    log "  $line"
  done <<<"$after_df"

  log "Docker maintenance complete. Total reclaimed space: $reclaimed"
}

main "$@"
