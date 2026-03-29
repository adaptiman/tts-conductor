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

## Fast Production Debug Loop

When investigating a production issue, use `vm/debug-loop.sh` to avoid waiting for the full CI/CD deploy cycle on every test.

What it does:
- Optionally fetches and checks out an experiment branch.
- Detects changed files and decides whether to rebuild launcher, bot image, both, or neither (`--mode auto`).
- Forces launcher runtime to `BOT_PULL_ON_START=false` during the debug run.
- Relaunches the bot through the internal launcher endpoint (`http://tts-launcher:8080/launch`) to bypass nginx/TLS noise.
- Prints bot status and filtered logs (`[highlight]`, `[utterance]`, `[speak]`, `instapaper`, errors).

Basic usage on the VM:

```bash
cd ~/tts-conductor

# Auto mode: infer what to rebuild from changed files
./vm/debug-loop.sh

# Pull and test an experiment branch
./vm/debug-loop.sh --branch debug/highlight-investigation --mode auto

# Force only bot rebuild + relaunch
./vm/debug-loop.sh --mode bot-only

# Force launcher-only rebuild/recreate
./vm/debug-loop.sh --mode launcher-only

# Relaunch only (no rebuild)
./vm/debug-loop.sh --mode no-build
```

Notes:
- This script is for fast troubleshooting on the VM and does not replace CI/CD for validated releases.
- For stable production behavior, merge validated changes to `main` and let the standard deployment workflow run.

---

## Production VM Troubleshooting

For VM runtime incidents (bot does not join, token rotation not reflected, launcher restart issues), use the VM operation scripts documented in `README.md` under the "VM operation scripts" section.

If bot logs show `Not authorized: exp-token`, the bot token is expired. Rotate the token and relaunch with:

```bash
cd ~/tts-conductor
./vm/rotate-daily-token-and-relaunch.sh instabot 24
```

Use `./vm/refresh-bot-token.sh` only after you have already rotated `DAILY_TOKEN` in `.env` and want to confirm the running container matches that value.

### Automated Daily token rotation (systemd timer)

The VM can rotate tokens automatically via:
- `tts-daily-token-rotate.timer`
- `tts-daily-token-rotate.service`

Check timer/service status:

```bash
sudo systemctl status tts-daily-token-rotate.timer
sudo systemctl status tts-daily-token-rotate.service
```

View recent rotation logs:

```bash
sudo journalctl -u tts-daily-token-rotate.service -n 80 --no-pager
```

Run an immediate manual rotation via systemd:

```bash
sudo systemctl start tts-daily-token-rotate.service
```

If it fails, review the script output first, then check launcher health/status endpoints:

```bash
cd ~/tts-conductor/vm
secret="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' tts-launcher | awk -F= '/^LAUNCHER_SHARED_SECRET=/{print substr($0,index($0,$2)); exit}')"
curl -sk -H "x-job-launcher-secret: $secret" https://localhost:8443/health
curl -sk -H "x-job-launcher-secret: $secret" https://localhost:8443/status
```