#!/usr/bin/env bash
set -euo pipefail

# Resolve directory of this script, then repo root = parent dir
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load environment variables from repo-root .env if present
if [[ -f "$REPO_ROOT/.env" ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +o allexport
fi

# Prefer GH_TOKEN from env/.env, fall back to GITHUB_TOKEN if set
if [[ -n "${GH_TOKEN-}" ]]; then
  export GH_TOKEN
elif [[ -n "${GITHUB_TOKEN-}" ]]; then
  export GITHUB_TOKEN
fi

command -v gh >/dev/null 2>&1 || { echo "ERROR: 'gh' CLI is required. Install from https://cli.github.com"; exit 1; }

KEEP="${KEEP:-50}"
DRY_RUN="${DRY_RUN:-true}"

echo "Using KEEP=$KEEP, DRY_RUN=$DRY_RUN"

# Always run gh against the repo root
cd "$REPO_ROOT"

REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"

workflow_ids=$(gh api \
  -H "Accept: application/vnd.github+json" \
  "/repos/$REPO/actions/workflows" \
  --paginate | jq -r '.workflows[].id' 2>/dev/null)

if [[ -z "$workflow_ids" ]]; then
  echo "No workflows found in $REPO"
  exit 0
fi

for wf_id in $workflow_ids; do
  echo "Processing workflow ID $wf_id in $REPO"

  runs=$(gh api \
    -H "Accept: application/vnd.github+json" \
    "/repos/$REPO/actions/workflows/$wf_id/runs?status=completed&per_page=100" \
    --paginate | jq -r '.workflow_runs[].id' 2>/dev/null)

  if [[ -z "$runs" ]]; then
    echo "  No completed runs found; skipping."
    continue
  fi

  mapfile -t run_array <<< "$runs"
  total=${#run_array[@]}

  echo "  Found $total completed runs."

  if (( total <= KEEP )); then
    echo "  Total <= KEEP ($KEEP); nothing to delete."
    continue
  fi

  to_delete=("${run_array[@]:$KEEP}")

  echo "  Will delete ${#to_delete[@]} runs (keeping $KEEP)."

  for run_id in "${to_delete[@]}"; do
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "  [DRY RUN] Would delete run $run_id"
    else
      echo "  Deleting run $run_id"
      gh api \
        -X DELETE \
        -H "Accept: application/vnd.github+json" \
        "/repos/$REPO/actions/runs/$run_id" || echo "    Failed to delete run $run_id"
      sleep 0.1
    fi
  done
done
