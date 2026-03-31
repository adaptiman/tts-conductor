# Instapaper Assistant Voice Controlled App

[![Watch demo video](https://github.com/adaptiman/tts-conductor/releases/download/0.8.0328.1/tts-conductor-thumbnail.jpg)](https://github.com/adaptiman/tts-conductor/releases/download/0.8.0328.1/Instapaper-Assistant.mp4)

A Python-based 100% audio controlled application for Instapaper. This application started out as a Python console app and has evolved to include voice command support with multiple STT/TTS options and a Daily WebRTC transport mode for cloud/VM deployment.

Docs site (GitHub Pages): https://adaptiman.github.io/tts-conductor/

## Architecture

The application is built with a modular design:

- **`article_manager.py`** - Contains the `ArticleManager` class with all Instapaper functionality
- **`ip_conductor.py`** - STT/TTS and console interface that uses the `ArticleManager` class
- **`example_usage.py`** - Demonstrates how to use `ArticleManager` in other programs

This design allows you to easily integrate Instapaper functionality into other Python applications by importing the `ArticleManager` class.

## Setup

### Prerequisites
- Python 3.12 or higher
- pip and venv

### Installation

1. Clone or download this repository:
   ```bash
   cd /path/to/ip-conductor
   ```

2. Create a virtual environment:
   ```bash
   python3 -m venv .venv
   ```

3. Activate the virtual environment:
   ```bash
   source .venv/bin/activate  # On Linux/macOS/WSL
   # or
   .venv\Scripts\activate     # On Windows
   ```

4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

5. Download the spaCy language model:
   ```bash
   python -m spacy download en_core_web_sm
   ```

### Configuration

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your Instapaper credentials:
   ```bash
   INSTAPAPER_USERNAME=your_email@example.com
   INSTAPAPER_PASSWORD=your_password
   INSTAPAPER_CONSUMER_KEY=your_consumer_key
   INSTAPAPER_CONSUMER_SECRET=your_consumer_secret
   ```

3. (Optional) Configure speak mode line width:
   ```bash
   SPEAK_LINE_WIDTH=70  # Default is 70 characters
   ```

   This controls how text wraps in speak mode. Adjust based on your terminal width and reading preference.

4. (Optional) Configure voice command + TTS services:
   ```bash
   DAILY_ROOM_URL=https://your-domain.daily.co/your-room
   DAILY_TOKEN=your_daily_token
   DAILY_API_KEY=your_daily_api_key
   DEEPGRAM_API_KEY=your_deepgram_api_key

   # Select default TTS vendor for Daily mode
   IP_CONDUCTOR_TTS_VENDOR=cartesia  # or elevenlabs

   # Cartesia
   CARTESIA_API_KEY=your_cartesia_api_key
   CARTESIA_VOICE_ID=your_cartesia_voice_id

   # ElevenLabs
   ELEVENLABS_API_KEY=your_elevenlabs_api_key
   ELEVENLABS_VOICE_ID=your_elevenlabs_voice_id

   # Optional: choose local mic device index for --voice --voice-transport local
   IP_CONDUCTOR_INPUT_DEVICE_INDEX=0

   # Optional: Daily/headless empty-room auto-shutdown window
   EMPTY_ROOM_SHUTDOWN_SECONDS=45
   ```

5. (Optional) Tune voice turn-taking, failover, and telemetry defaults:
   ```bash
   IP_CONDUCTOR_TURN_PROFILE=balanced
   IP_CONDUCTOR_BARGE_IN_MODE=commands
   IP_CONDUCTOR_COMMAND_EMIT_SOURCE=turn_stop
   IP_CONDUCTOR_IDLE_TIMEOUT_SECONDS=120

   IP_CONDUCTOR_STT_PROVIDER=deepgram
   IP_CONDUCTOR_STT_KEEPALIVE_SECONDS=20
   IP_CONDUCTOR_STT_ENDPOINTING_MS=250
   IP_CONDUCTOR_STT_UTTERANCE_END_MS=700

   IP_CONDUCTOR_TTS_CONCURRENCY=1
   IP_CONDUCTOR_TTS_TEXT_AGGREGATION_MODE=sentence

   IP_CONDUCTOR_FAILOVER_ENABLED=true
   IP_CONDUCTOR_FAILOVER_CHAIN=deepgram,whisper
   IP_CONDUCTOR_METRICS_ENABLED=true
   ```

For the complete and current environment variable list, see `.env.example`.

For VM launcher compose settings, see `vm/.env.example`.

**Note**: Never commit your `.env` file to version control. It's already included in `.gitignore`.

## Usage

Activate the virtual environment (if not already active):
```bash
source .venv/bin/activate
```

Run the application:
```bash
python ip_conductor.py
```

List microphone devices (useful for local voice mode setup):
```bash
python ip_conductor.py --list-audio-devices
```

Run with voice commands using local microphone + local Whisper:
```bash
python ip_conductor.py --voice --voice-transport local
```

Run with voice commands + Daily transport and pick a TTS vendor for this session:
```bash
python ip_conductor.py --voice --voice-transport daily --tts-vendor cartesia
python ip_conductor.py --voice --voice-transport daily --tts-vendor elevenlabs
```

Run in headless mode (for container/cloud runtime):
```bash
python ip_conductor.py --voice --voice-transport daily --headless
```

### CLI Reference

Use `python ip_conductor.py --help` to view the current command-line options.

Available options:

- `-h, --help`
- `--voice`
- `--voice-transport {local,daily}`
- `--tts-vendor {cartesia,elevenlabs}`
- `--turn-profile {fast,balanced,safe}`
- `--barge-in-mode {off,commands,always}`
- `--command-emit-source {interim,final,turn_stop}`
- `--idle-timeout-seconds IDLE_TIMEOUT_SECONDS`
- `--stt-provider {deepgram,whisper}`
- `--stt-keepalive-seconds STT_KEEPALIVE_SECONDS`
- `--stt-endpointing-ms STT_ENDPOINTING_MS`
- `--stt-utterance-end-ms STT_UTTERANCE_END_MS`
- `--tts-concurrency TTS_CONCURRENCY`
- `--tts-text-aggregation {token,sentence}`
- `--failover | --no-failover`
- `--failover-chain FAILOVER_CHAIN`
- `--metrics | --no-metrics`
- `--daily-room-url DAILY_ROOM_URL`
- `--daily-token DAILY_TOKEN`
- `--headless`
- `--list-audio-devices`

In Daily + headless mode, the bot now auto-shuts down after the room is empty
for a grace period (default: 45 seconds). This helps container platforms scale
down when nobody is connected.

You can change the grace period with:
```bash
EMPTY_ROOM_SHUTDOWN_SECONDS=90
```

Optional Daily viewer page:

- Open `web/daily_console_viewer.html` in a browser.
- Enter the same Daily room URL/token to view mirrored console lines and hear room audio.

### Turn Strategy and Latency Tuning

The voice command UX does **not** require a wake phrase. You continue saying
normal commands such as `next`, `pause`, `read`, and `stop`.

Tune command timing and interruption behavior with environment variables:

```bash
# Turn handling
IP_CONDUCTOR_TURN_PROFILE=balanced            # fast | balanced | safe
IP_CONDUCTOR_COMMAND_EMIT_SOURCE=turn_stop    # interim | final | turn_stop
IP_CONDUCTOR_BARGE_IN_MODE=commands           # off | commands | always
IP_CONDUCTOR_IDLE_TIMEOUT_SECONDS=120

# STT tuning
IP_CONDUCTOR_STT_PROVIDER=deepgram            # deepgram | whisper
IP_CONDUCTOR_STT_KEEPALIVE_SECONDS=20
IP_CONDUCTOR_STT_ENDPOINTING_MS=250
IP_CONDUCTOR_STT_UTTERANCE_END_MS=700

# TTS tuning
IP_CONDUCTOR_TTS_CONCURRENCY=1
IP_CONDUCTOR_TTS_TEXT_AGGREGATION_MODE=sentence   # token | sentence

# Failover + telemetry
IP_CONDUCTOR_FAILOVER_ENABLED=true
IP_CONDUCTOR_FAILOVER_CHAIN=deepgram,whisper
IP_CONDUCTOR_METRICS_ENABLED=true
```

Equivalent CLI flags are available when launching `ip_conductor.py`:

```bash
--turn-profile fast|balanced|safe
--barge-in-mode off|commands|always
--command-emit-source interim|final|turn_stop
--idle-timeout-seconds <int>
--stt-provider deepgram|whisper
--stt-keepalive-seconds <int>
--stt-endpointing-ms <int>
--stt-utterance-end-ms <int>
--tts-concurrency <int>
--tts-text-aggregation token|sentence
--failover / --no-failover
--failover-chain deepgram,whisper
--metrics / --no-metrics
```

Examples:

```bash
# Balanced defaults, command-only barge-in
python ip_conductor.py --voice --voice-transport daily --barge-in-mode commands

# Fast reaction profile with interim command emission
python ip_conductor.py --voice --voice-transport daily --turn-profile fast --command-emit-source interim

# Conservative behavior (no interruption while speaking)
python ip_conductor.py --voice --voice-transport daily --barge-in-mode off --turn-profile safe
```

Troubleshooting:

- If commands feel delayed, try `--turn-profile fast` or `--command-emit-source interim`.
- If commands trigger too often, try `--turn-profile safe` and `--command-emit-source turn_stop`.
- If speech gets cut off too often, use `--barge-in-mode off`.

### Daily Validation Runbook

Use this checklist to validate Daily transport behavior in a controlled session.
For release sign-off, use the copy-paste QA checklist in
`docs/voice-daily-validation-checklist.md`.

Prerequisites:

- A valid Daily room URL and token.
- `DEEPGRAM_API_KEY` set.
- If you want room speech output, valid TTS credentials for the selected vendor.

Recommended validation commands:

```bash
# Balanced profile (default operational mode)
python ip_conductor.py \
   --voice \
   --voice-transport daily \
   --turn-profile balanced \
   --barge-in-mode commands \
   --command-emit-source turn_stop \
   --metrics

# Safe profile (fewer accidental turn transitions)
python ip_conductor.py \
   --voice \
   --voice-transport daily \
   --turn-profile safe \
   --barge-in-mode off \
   --command-emit-source turn_stop \
   --metrics
```

Expected signals during validation:

- Startup shows voice listener initialization with Daily transport.
- Speaking commands emits `[voice] <command>` lines.
- Metrics lines (`[metrics] ...`) appear in logs when enabled.
- In safe profile, command acceptance should feel more conservative.

Suggested spoken validation script:

1. `read`
2. `pause`
3. `continue`
4. `back two`
5. `forward one`
6. `repeat`
7. `highlight`
8. `stop`

Optional failover drill:

- Start with `IP_CONDUCTOR_FAILOVER_ENABLED=true` and `IP_CONDUCTOR_FAILOVER_CHAIN=deepgram,whisper`.
- Force a primary STT failure (for example, temporarily revoke key/network access).
- Confirm logs include `service.stt_failover_switch` metric and service switch warning.

Capture logs for review:

```bash
python ip_conductor.py --voice --voice-transport daily --turn-profile balanced --metrics \
   2>&1 | tee /tmp/ip-conductor-daily.log
```

Quick log checks:

```bash
grep -E "\[voice\]|\[metrics\]|service\.stt_failover_switch|Invalid RTVI transport message" /tmp/ip-conductor-daily.log
```

If you see repeated `Invalid RTVI transport message` warnings:

- Verify room URL/token pair belongs to the same room and has proper permissions.
- Test with a clean room and a single participant first.
- Confirm no external client is sending malformed RTVI payloads without required fields.

### Release Deployment (Production)

Use these steps to deploy a tagged release to production.

1. Update the repo and check out the release tag:

```bash
cd /path/to/tts-conductor
git fetch origin --tags
git checkout main
git pull --ff-only origin main
git checkout 0.8.0324.1
```

2. Rebuild and restart with Docker Compose (if you use `vm/docker-compose.yml`):

```bash
cd /path/to/tts-conductor
docker compose -f vm/docker-compose.yml down
docker compose -f vm/docker-compose.yml build --no-cache
docker compose -f vm/docker-compose.yml up -d
```

3. Rebuild and restart with plain Docker (if you run a single container):

```bash
cd /path/to/tts-conductor
docker build -t tts-conductor:0.8.0324.1 .
docker stop tts-conductor || true
docker rm tts-conductor || true
docker run -d \
   --name tts-conductor \
   --restart unless-stopped \
   --env-file .env \
   tts-conductor:0.8.0324.1
```

4. Verify the deployment:

```bash
docker ps
docker logs --tail=200 tts-conductor
```

### Configuration Profiles (Docker Launcher)

For VM launcher deployments, runtime behavior is controlled by two settings in
`vm/docker-compose.yml`:

- `BOT_COMMAND`: CLI flags passed to `ip_conductor.py`.
- `BOT_ENV_SOURCE`: host env file mounted into launcher as bot runtime env.

Recommended profile files on the host:

- `.env.prod-balanced`
- `.env.prod-safe`
- `.env.prod-fast`

Set `BOT_ENV_SOURCE` to one of those files and set `BOT_COMMAND` to one of the
presets below.

Balanced profile (default operational mode):

```bash
BOT_COMMAND=python ip_conductor.py --voice --voice-transport daily --headless --turn-profile balanced --barge-in-mode commands --command-emit-source turn_stop --metrics
```

Safe profile (fewer accidental triggers):

```bash
BOT_COMMAND=python ip_conductor.py --voice --voice-transport daily --headless --turn-profile safe --barge-in-mode off --command-emit-source turn_stop --metrics
```

Fast profile (lowest command latency):

```bash
BOT_COMMAND=python ip_conductor.py --voice --voice-transport daily --headless --turn-profile fast --barge-in-mode commands --command-emit-source interim --metrics
```

Example env source selection:

```bash
BOT_ENV_SOURCE=../.env.prod-safe
```

Apply launcher configuration changes:

```bash
cd /path/to/tts-conductor
docker compose -f vm/docker-compose.yml up -d --build --force-recreate tts-launcher
```

Then trigger launch/relaunch through the launcher endpoint/workflow so the bot
container is recreated with the updated command and environment.

### Available Commands

#### Article Management
- `articles` / `bookmarks` / `a` - List all articles with numbers (up to 25 by default)
- `add` - Add a new article by entering a URL
- `delete` / `d` - Delete the currently selected article
- `star` / `s` - Star the currently selected article
- `archive` / `c` - Archive the currently selected article
- `highlight` - Create a highlight for the current article (multi-line text input)
- `speak` / `k` - Enter sentence-by-sentence reading mode with highlighting support
- `speak <number>` / `k <number>` - Navigate to and speak a specific article by its number

#### Navigation
- `title` - Show current article title
- `<number>` - Navigate to article by number and display its title (e.g., `5` jumps to article 5)
- `next` / `n` - Move to next article
- `prev` / `previous` / `p` - Move to previous article
- `first` - Jump to first article
- `last` - Jump to last article
- `read` / `r` - Read current article content
- `read <number>` / `r <number>` - Navigate to and read a specific article by its number from the list

#### System
- `exit` - Quit the application

### Voice Commands (When `--voice` Is Enabled)

The following spoken commands are available:

- `next`, `previous`, `first`, `last` - Bookmark navigation
- `delete`, `archive` - Apply to current article
- `read` - Start voice read mode
- `pause`, `continue`, `stop` - Read-mode control
- `highlight` / `mark` - Highlight the current spoken sentence
- `back`, `back <number>` - Jump backward by sentence in read mode
- `forward`, `forward <number>` - Jump forward by sentence in read mode
- `repeat` - Replay the current sentence in read mode

### Keyboard Shortcuts

For faster navigation, single-letter shortcuts are available for common commands:
- `a` - Articles/bookmarks list
- `n` - Next article
- `p` - Previous article
- `d` - Delete current article
- `s` - Star current article
- `c` - Archive current article
- `r` - Read current article
- `k` - Speak current article

**With article numbers:**
- `r 3` - Read article 3
- `k 5` - Speak article 5

### Speak Mode (Keyboard)

Speak mode provides an interactive sentence-by-sentence reading experience with intelligent sentence parsing powered by spaCy:

1. Enter speak mode: `speak`
2. Navigate and highlight using keyboard commands:
   - **SPACE** - Display next sentence
   - **B** - Go back to previous sentence
   - **H** - Highlight current sentence (saves to Instapaper)
   - **Q** - Quit speak mode

Each sentence displays with its position in the article:
```
[sentence_number/total_sentences]
Sentence text appears here.
```

When you highlight a sentence with **H**, a confirmation message appears, and you can continue navigating with SPACE or B.

### Read Mode (Voice)

When voice mode is enabled (`--voice`), say `read` to start continuous sentence playback.

- Say `stop` to exit read mode
- Say `pause` / `continue` to pause and resume
- Say `back`, `forward`, or `repeat` for sentence-level control
- Say `highlight` / `mark` to save the current sentence as a highlight
- Say `delete` or `archive` to apply article-level actions

### Features

- **Environment-based configuration**: Secure credential storage using `.env` files
- **Virtual environment support**: Isolated dependencies per project
- **Numbered article listing**: Articles are displayed with numbers for easy reference
- **Quick navigation**: Jump to any article by simply entering its number
- **Direct article access**: Jump to and read any article by its number
- **Speak mode**: Interactive sentence-by-sentence reading with NLP-powered sentence parsing
- **Smart highlighting**: Highlight sentences directly from speak mode with automatic syncing to Instapaper
- **Configurable article limit**: The application fetches 25 articles by default (configurable in `ArticleManager` initialization)
- **Error handling**: Comprehensive error handling for network issues, API errors, and invalid operations
- **Interactive highlights**: Create multi-line highlights by entering text and pressing Enter twice to finish
- **Voice-enabled read mode**: Optional spoken controls for read, pause, continue, highlight, delete, and archive

### Example Workflow

```bash
# Activate the virtual environment
source .venv/bin/activate

# Start the application
python ip_conductor.py

# List all articles with numbers (using shortcut)
> a
1. Understanding Python Decorators
2. Introduction to Machine Learning
3. Web Development Best Practices
4. Advanced Git Techniques
5. Docker for Beginners

# Quick jump to article 3 by entering just the number
> 3
Web Development Best Practices

# Read the current article using shortcut
> r

# Or jump and read in one command using shortcut
> r 5
[Displays content of "Docker for Beginners"]

# Enter speak mode for sentence-by-sentence reading using shortcut
> k
[1/350]
Docker is a platform for developing applications.
# Press SPACE to see next sentence
# Press H to highlight current sentence
# Press B to go back to previous sentence
# Press Q to quit speak mode

# Or speak a specific article directly
> k 2
[Opens speak mode for article 2: "Introduction to Machine Learning"]

# Navigate to next article using shortcut
> n
[Now at article 6]

# Create a highlight
> highlight
Enter the text you want to highlight (press Enter twice to finish):
This is important text
that I want to remember.

# Star the article using shortcut
> s

# Archive when done using shortcut
> c

# Exit
> exit
```

## Dependencies

### Core Application Dependencies
- `instapaper==0.5` - Instapaper API client for bookmark management
- `oauth2==1.9.0.post1` - OAuth authentication for Instapaper API
- `httplib2==0.31.0` - HTTP client library for API requests
- `python-dotenv==1.2.1` - Environment variable management for configuration
- `spacy==3.8.11` - Natural language processing for sentence parsing
- `en-core-web-sm` - English language model for spaCy (downloaded separately)
- `setuptools==80.9.0` - Python package utilities (required for Python 3.12+)

### Voice + Realtime Dependencies
- `pipecat-ai[local,whisper,websockets-base,elevenlabs]==0.0.105` - Voice pipelines (Whisper STT, Daily transport, Cartesia/ElevenLabs TTS)
- `daily-python==0.24.0` - Daily WebRTC transport
- `deepgram-sdk==6.0.1` - Deepgram speech-to-text for Daily mode
- `pyaudio==0.2.14` - Local microphone input
- `aiohttp==3.13.3` - Async HTTP client used by voice transport services
- `loguru==0.7.3` - Structured runtime logging

### Development and Code Quality Tools
- `black==25.11.0` - Code formatter for consistent Python code style
- `flake8==7.3.0` - Style guide enforcement (PEP 8 compliance)
- `isort==7.0.0` - Import statement organizer and sorter
- `mypy==1.18.2` - Static type checker for Python
- `pylint==4.0.3` - Comprehensive code analysis and linting

### Additional Dependencies
The application also includes various supporting packages for spaCy, HTTP handling, and data processing. See `requirements.txt` for the complete list of dependencies with exact versions.

All dependencies are listed in `requirements.txt` and will be installed automatically with `pip install -r requirements.txt`.

## Customization

### Article Limit
To change the number of articles fetched, pass a different limit when creating the `ArticleManager` instance:

```python
# In ip_conductor.py main() function
manager = ArticleManager(bookmark_limit=50)  # Change from default 25 to 50
```

## Using ArticleManager in Other Programs

The `ArticleManager` class can be easily imported and used in other Python applications. Make sure your `.env` file is properly configured in your project directory.

```python
from article_manager import ArticleManager

# Create an instance
manager = ArticleManager(bookmark_limit=25)

# Get article information
title = manager.get_current_title()
article_text = manager.get_current_article()
article_list = manager.get_bookmarks_list()

# Navigate articles
manager.next_bookmark()
manager.prev_bookmark()
manager.first_bookmark()
manager.last_bookmark()

# Jump to a specific article by number (1-based)
manager.set_bookmark_by_number(5)

# Manage articles
success, url, error = manager.add_bookmark_url("https://example.com")
success, title, error = manager.star_current_bookmark()
success, title, error = manager.archive_current_bookmark()
success, title, error = manager.delete_current_bookmark()

# Create highlights
success, title, highlight, error = manager.create_highlight_for_current("Important text")

# Parse article into sentences for speak mode functionality
sentences = manager.parse_current_article_sentences()
# Returns list of sentence strings: ["First sentence.", "Second sentence.", ...]

# Access the Instapaper client directly for advanced operations
bookmarks = manager.instapaper_client.bookmarks(limit=10)
```

See `example_usage.py` for a complete demonstration of using `ArticleManager` programmatically.

### Adding New Commands
The application is designed to be easily extensible. To add new commands:

1. Add a new method to the `ArticleManager` class in `article_manager.py`
2. Add error handling using try-except blocks with appropriate exception types
3. Add a command handler function in `ip_conductor.py` (following the pattern of existing handlers)
4. Add the command to the main command loop in the `run_console()` function
5. Update the help messages to include the new command

## Development Tools and Code Quality

This project includes comprehensive code quality tools to maintain clean, consistent, and error-free Python code:

### Available Tools
- **Black**: Automatic code formatting for consistent style
- **isort**: Import statement organization and sorting
- **Flake8**: Style guide enforcement (PEP 8 compliance)
- **Pylint**: Comprehensive code analysis and quality checking
- **Mypy**: Static type checking for better code reliability

### Usage

#### Run all linting and formatting tools:
```bash
./lint.sh
```

#### Run lint checks in CI mode (no file rewrites):
```bash
./lint.sh --check
```

#### Run individual tools:
```bash
# Format code automatically
black ip_conductor.py article_manager.py

# Sort and organize imports
isort ip_conductor.py article_manager.py

# Check code style (PEP 8)
flake8 ip_conductor.py article_manager.py

# Comprehensive code analysis
pylint ip_conductor.py article_manager.py

# Static type checking
mypy ip_conductor.py article_manager.py --ignore-missing-imports
```

### Configuration
- **`.flake8`**: Flake8 configuration with 88-character line length
- **`pyproject.toml`**: Centralized configuration for Black, isort, Pylint, and Mypy
- **`lint.sh`**: Convenient script to run all tools in sequence

### VS Code Integration
The project includes VS Code settings that integrate these tools for real-time feedback. Install these recommended extensions:
- Python (ms-python.python)
- Pylint (ms-python.pylint)
- Black Formatter (ms-python.black-formatter)
- isort (ms-python.isort)
- Mypy Type Checker (ms-python.mypy-type-checker)

For more details, see `LINTING.md`.

## VS Code Setup (WSL)

If you're using VS Code with WSL, the project includes VS Code settings in `.vscode/settings.json` that will:
- Automatically use the project's virtual environment
- Activate the venv when opening new terminals

This provides seamless integration without manual activation within VS Code.

## GitHub Actions and VM Deployment

This repository now uses a VM-only runtime and build model.

Current workflow file:
- `.github/workflows/ci-vm.yml` (CI checks on push/PR plus production VM deploy on `main` pushes)

Treat CI checks as authoritative and prefer VM-local build/restart scripts for runtime updates.

### Current Production Architecture (VM-native webhook ingress)

Production runtime now centers on the VM launcher stack:
- **Daily domain webhook**: Daily webhook delivery calls the VM HTTPS endpoint directly.
- **VM launcher stack**: persistent Docker Compose services on the VM (`tts-launcher` and `tts-launcher-proxy`) validate webhook requests and start/stop bot containers on demand.
- **Bot runtime container**: created per launch request, joins Daily room, and exits after room-empty grace logic.

For an end-to-end user session flow (room join -> Instapaper navigation/read commands -> room leave), see [USER_JOURNEY.md](USER_JOURNEY.md).

### VM webhook endpoints

The VM launcher exposes:
- `POST /daily-hook`: Handles Daily webhook events and maps them to bot start/stop actions.
- `POST /launch`: Manual launch endpoint.
- `POST /stop`: Manual stop endpoint.
- `GET /status`: Bot/launcher status endpoint.
- `GET /health`: Launcher health endpoint.

Daily webhook auth options:
- `DAILY_HOOK_HMAC_SECRET` (preferred): validates Daily webhook signatures (`x-webhook-signature` + `x-webhook-timestamp`; legacy `x-daily-signature` also accepted).
- `DAILY_HOOK_SHARED_SECRET` (fallback): validates shared secret from header, bearer token, query `secret`, or payload `secret`.

Optional webhook behavior controls:
- `DAILY_WEBHOOK_ROOM_NAME` (ignore events for other rooms)
- `DAILY_HOOK_ENABLE_STOP_ACTION` (defaults to `false`)
- `DAILY_HOOK_START_ON_UNRECOGNIZED_EVENT` (defaults to `true`)

### Daily webhook automation (recommended)

Configure/update your Daily webhook URL to call:

```text
https://cookbook.thesweeneys.org:8443/daily-hook
```

Recommended auth mode for Daily hooks:
- Configure Daily webhooks to sign payloads with your HMAC key.
- Set the same key in VM launcher env as `DAILY_HOOK_HMAC_SECRET`.
- When `DAILY_HOOK_HMAC_SECRET` is set, launcher requires a valid Daily signature header and does not use shared-secret fallback.

Event mapping:
- `meeting.started` (or `first_non_owner_join=true`) -> launcher starts the VM bot container.
- `participant.left` and `meeting.ended` -> launcher stops the VM bot container **only if** `DAILY_HOOK_ENABLE_STOP_ACTION=true`.

Default behavior is start-only via webhook. This avoids unreliable stop semantics in rooms where the bot itself can keep the room non-empty; shutdown remains controlled by the bot's in-app idle logic.

You can force a specific action with query parameters when needed:
- `.../daily-hook?action=start`
- `.../daily-hook?action=stop`

### HMAC key rotation workflow

Use this sequence to rotate webhook signing keys safely:
1. Generate a new key and update `DAILY_HOOK_HMAC_SECRET` in `vm/.env` on the VM.
2. Recreate launcher services so the new env is loaded:

```bash
cd ~/tts-conductor/vm
docker compose up -d --force-recreate tts-launcher tts-launcher-proxy
```

3. Update the Daily webhook signing key to the same value.
4. Trigger a test event (or use a room join) and verify launcher receives and handles it.

If your Daily setup cannot sign hooks yet, temporary fallback is:
- Set `DAILY_HOOK_SHARED_SECRET` and use `?secret=...` or header/bearer auth.
- Prefer returning to HMAC mode after cutover.

### Manual launch endpoint (optional)

Call the VM endpoint directly:

```bash
curl -X POST "https://cookbook.thesweeneys.org:8443/launch" \
   -H "x-job-launcher-secret: <your-shared-secret>"
```

Response behavior:
- Starts bot container (`started: true`) when no active container exists.
- Returns `started: false` with active container metadata when one is already running.

### VM launcher stack (Docker Compose on the VM)

VM files are under `vm/`:
- `vm/docker-compose.yml`
- `vm/nginx/launcher.conf`
- `vm/launcher/app.py`

The stack includes:
- `tts-launcher` (FastAPI service controlling Docker on the host via `/var/run/docker.sock`)
- `tts-launcher-proxy` (nginx TLS proxy on port `8443`)
- Separate Docker networks (`tts-launcher-internal`, `tts-conductor-bot-net`) so this stack does not share networks with `openeats`

On the VM:

```bash
cd ~/tts-conductor/vm
cp .env.example .env
# Edit vm/.env and set JOB_LAUNCHER_SHARED_SECRET plus any bot limits.

# Build the bot image locally on the VM.
docker build -t tts-conductor:local ..

docker compose up -d --build
```

Notes:
- `BOT_PULL_ON_START` defaults to `false` for the VM launcher stack and should stay `false` for VM-local image operation.
- Refresh the bot image during deploy/maintenance with a local rebuild (`docker build -t tts-conductor:local ..`).
- If you set `BOT_PULL_ON_START=true`, ensure `BOT_IMAGE` points to an accessible registry image.

### VM operation scripts

The `vm/` folder includes helper scripts for common production operations:
- `vm/update-production.sh`: Pull latest repo changes, build the bot image locally, validate launcher health, and relaunch the bot when needed.
- `vm/tts-conductor-restart.sh`: Force recreate launcher services and trigger a bot launch.
- `vm/docker-maintenance.sh`: Prune Docker resources and log reclaimed space to `/etc/tts-conductor/docker-maintenance.log`.
- `vm/refresh-bot-token.sh`: Restart launcher services, relaunch the bot, and verify the running bot token hash matches `.env`.
- `vm/rotate-daily-token-and-relaunch.sh`: Generate a fresh Daily meeting token, update the bot `.env`, recreate the launcher stack, and relaunch the bot — use after a Daily token expires.
- `vm/cleanup-workflow-runs.sh`: Delete old GitHub Actions workflow runs via the GitHub API to keep the Actions history manageable. Requires `GH_TOKEN` (or `GITHUB_TOKEN`) in the repo-root `.env` and the [`gh` CLI](https://cli.github.com) installed. Defaults: keep 50 runs, dry-run mode on.

  ```bash
  # Preview what would be deleted (dry run, keep newest 20):
  KEEP=20 DRY_RUN=true ./vm/cleanup-workflow-runs.sh

  # Actually delete, keeping newest 20:
  KEEP=20 DRY_RUN=false ./vm/cleanup-workflow-runs.sh
  ```

For the end-to-end in-room user command flow, see [USER_JOURNEY.md](USER_JOURNEY.md).

Use `vm/refresh-bot-token.sh` after rotating `DAILY_TOKEN` in the repository root `.env` on the VM:

```bash
cd ~/tts-conductor
./vm/refresh-bot-token.sh
```

Useful overrides for `vm/refresh-bot-token.sh`:
- `WAIT_SECONDS=<n>` to adjust startup wait time for `tts-conductor-bot`.
- `ROOT_ENV_FILE`, `VM_ENV_FILE`, `LAUNCH_URL`, `STATUS_URL`, `BOT_CONTAINER_NAME` for non-default paths/endpoints.

For incident response steps, see `TROUBLESHOOTING.md` -> "Production VM Troubleshooting".

If NSG ingress is missing, allow TCP 8443 on the VM NSG.

TODO: move runtime secrets out of plain `.env` into a controlled secret solution (for example Key Vault or Docker Swarm/Kubernetes secrets).

### Bot Lifecycle Mode

Current intended production behavior for cost + responsiveness:
- `tts-launcher` and `tts-launcher-proxy` are always running on the VM.
- The bot container is **not** always in the Daily room.
- The bot starts when a Daily room hook hits the VM endpoint (`/daily-hook` -> `/launch`).
- After the last non-bot participant leaves, the bot exits after the in-app grace window (currently 45 seconds).
- The VM launcher stack stays up and ready for the next webhook event.

Why this mode:
- Avoids external relay dependencies in the launch path.
- Avoids unnecessary Daily room-active minutes (and cost) when no humans are present.

Verified behavior to preserve:
- Join room -> hook received -> bot joins.
- Last human leaves -> bot exits after ~45s.
- Launcher services remain healthy on VM after bot exits.
- Rejoin requires a fresh room-hook-triggered launch.

### Container runtime defaults

The included `Dockerfile` starts the app in voice + Daily + headless mode:
```bash
python ip_conductor.py --voice --voice-transport daily --headless
```

When running this default command, the process exits automatically after all
remote Daily participants leave and the room remains empty for the configured
grace period (45s by default).

Adjust `BOT_COMMAND` in `vm/.env` (or the defaults in `vm/docker-compose.yml`) if you want a different transport or startup behavior.

## Troubleshooting

### Missing Environment Variables
If you see an error about missing environment variables, ensure:
1. Your `.env` file exists in the project root
2. All four required variables are set (USERNAME, PASSWORD, CONSUMER_KEY, CONSUMER_SECRET)
3. There are no extra spaces or quotes around the values

### Import Errors in WSL Terminal
If you get import errors when running from a WSL terminal outside VS Code:
```bash
cd /path/to/ip-conductor
source .venv/bin/activate
```

The virtual environment must be activated to access the installed packages.

## License

This project is licensed under the Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International license. See [LICENSE](LICENSE.md).