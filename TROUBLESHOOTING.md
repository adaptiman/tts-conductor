# Automatic File Changes Troubleshooting

If you're experiencing automatic changes to files after committing (especially whitespace changes), here are the most common causes and solutions:

## Common Causes

### 1. VS Code Auto-Formatting
- **Format on Save**: `"editor.formatOnSave": true`
- **Trim Trailing Whitespace**: `"files.trimTrailingWhitespace": true` 
- **Auto Whitespace**: `"editor.trimAutoWhitespace": true`

### 2. Git Hooks
- Pre-commit hooks running formatters
- Post-commit hooks
- Check `.git/hooks/` directory

### 3. VS Code Extensions
- Markdown formatters
- Prettier
- Auto-formatting extensions

### 4. Editor Config
- `.editorconfig` files with trim settings
- Language-specific formatting rules

## Solutions Implemented

### 1. Updated VS Code Settings (`.vscode/settings.json`)
```json
{
    "editor.formatOnSave": false,
    "editor.trimAutoWhitespace": false,
    "files.trimTrailingWhitespace": false,
    "[markdown]": {
        "editor.formatOnSave": false,
        "editor.trimAutoWhitespace": false,
        "files.trimTrailingWhitespace": false
    }
}
```

### 2. Created `.editorconfig`
- Disables trailing whitespace trimming for Markdown files
- Maintains consistent behavior across editors

### 3. Manual Control
- Use `./lint.sh` for intentional formatting
- Or run `black` / `isort` manually on specific files when needed

## Testing the Fix

1. Make a small change to README.md
2. Save the file
3. Commit the change  
4. Check if automatic changes still occur

If problems persist, check:
- Global VS Code settings (`Ctrl+Shift+P` > "Preferences: Open Settings (JSON)")
- Installed VS Code extensions
- Git configuration for hooks

## GitHub Actions: Cleaning Up Old Workflow Runs

Over time, the Actions history can accumulate hundreds of runs, making the GitHub UI slow to load. The script `vm/cleanup-workflow-runs.sh` deletes completed runs beyond a configurable threshold.

**Prerequisites:**
- [`gh` CLI](https://cli.github.com) installed on the machine running the script.
- A GitHub personal access token with `repo` scope (or a fine-grained token with `Actions: write` permission) stored as `GH_TOKEN` in the repo-root `.env`:
  ```
  GH_TOKEN=github_pat_...
  ```

**Usage:**

```bash
cd ~/tts-conductor

# Preview what would be deleted, keeping the newest 20 runs per workflow:
KEEP=20 DRY_RUN=true ./vm/cleanup-workflow-runs.sh

# Actually delete, keeping the newest 20:
KEEP=20 DRY_RUN=false ./vm/cleanup-workflow-runs.sh
```

Environment variables (all optional, can be set inline or in `.env`):

| Variable | Default | Description |
|----------|---------|-------------|
| `KEEP` | `50` | Number of most-recent completed runs to preserve per workflow |
| `DRY_RUN` | `true` | Set to `false` to perform deletions; `true` only reports what would be deleted |
| `GH_TOKEN` | _(from `.env`)_ | GitHub API token; falls back to `GITHUB_TOKEN` if set |

The script iterates every workflow in the repository, orders runs newest-first (as returned by the GitHub API), skips workflows with `≤ KEEP` runs, and deletes the remainder one at a time with a small delay to avoid rate-limiting.

---

## Production VM Troubleshooting

For VM runtime incidents (bot does not join, token rotation not reflected, launcher restart issues), use the VM operation scripts documented in `README.md` under the "VM operation scripts" section.

Recommended first command after rotating `DAILY_TOKEN` in the VM's repository root `.env`:

```bash
cd ~/tts-conductor
./vm/refresh-bot-token.sh
```

What this script verifies:
- Re-authenticates host access to Azure/ACR (unless `SKIP_AZURE_LOGIN=true`)
- Recreates launcher services and relaunches `tts-conductor-bot`
- Confirms the running bot container's `DAILY_TOKEN` hash matches `.env`

If it fails, review the script output first, then check launcher health/status endpoints:

```bash
cd ~/tts-conductor/vm
secret="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' tts-launcher | awk -F= '/^LAUNCHER_SHARED_SECRET=/{print substr($0,index($0,$2)); exit}')"
curl -sk -H "x-job-launcher-secret: $secret" https://localhost:8443/health
curl -sk -H "x-job-launcher-secret: $secret" https://localhost:8443/status
```