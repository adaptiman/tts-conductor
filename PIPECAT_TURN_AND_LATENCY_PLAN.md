# Pipecat Turn Strategy and Latency Upgrade Plan (No Wake Phrase)

## Scope Decision

- Keep voice UX unchanged: users only say normal commands (no wake phrase).
- Implement turn/mute strategy architecture, faster response pipeline, failover, and observability.

## Per-File Concrete Change List

### 1) requirements.txt

Modify:
- Bump `pipecat-ai[local,whisper,websockets-base,elevenlabs]` from `0.0.105` to at least `0.0.106` (or latest stable selected for implementation).
- Keep current extras unless additional providers are explicitly introduced in this pass.
- Add a short inline comment that this version is required for newer turn/mute and failover APIs.

### 2) voice_commands.py

Modify `VoiceCommandProcessor`:
- Keep command mapping logic.
- Shift default command emission to one command per completed turn (instead of interim-first), controlled by config.
- Keep destructive-command debounce for delete/archive, but make debounce windows configurable.

Add `VoicePipelineConfig` dataclass near the top:
- Holds runtime knobs for turn profile, barge-in mode, emit source, idle timeout, STT tuning, TTS tuning, failover, and metrics.

Add `PipelineMetricsObserver` processor:
- Collects and logs command detect delay, turn duration, interruption count, and sentence/TTS timing proxies.

Add helper methods on `VoiceCommandListener`:
- `_build_stt_service(self)`
- `_build_tts_service(self)`
- `_build_turn_and_mute_processors(self)`
- `_build_failover_strategy(self)`
- `_emit_metric(self, name, value, tags)`
- `_apply_runtime_service_settings(self)`

Modify `VoiceCommandListener.__init__`:
- Accept new config inputs and normalize into `VoicePipelineConfig`.

Modify `_build_local_pipeline`:
- Insert turn/mute-related processors around STT and command processor.
- Add metrics observer into the pipeline chain.

Modify `_build_daily_pipeline`:
- Same turn/mute integration as local mode.
- Add optional failover strategy wrapper for STT/TTS.
- Add idle event support and remove legacy idle assumptions.

Modify `speak_text`:
- Attach/propagate `context_id` where supported to improve sentence-level traceability.

Modify `interrupt_tts`:
- Prefer pipeline-wide interruption API when available.
- Keep `InterruptionFrame` fallback for compatibility.

### 3) ip_conductor.py

Modify CLI parser:
- Add new flags listed in the CLI section below.

Modify `run_console(...)` signature and call sites:
- Accept new voice pipeline options and pass them to `VoiceCommandListener`.

Modify `_on_voice_command(...)` handling:
- Preserve existing command semantics.
- Respect configurable barge-in behavior:
  - `off`: do not interrupt active TTS
  - `commands`: interrupt only for actionable command turns
  - `always`: current behavior
- Preserve read-mode controls and highlight behavior.

### 4) output_adapter.py

Modify `SpeakingOutputAdapter` filtering:
- Skip telemetry lines prefixed with `[metrics]` and low-level transport diagnostics.
- Prevent metric/status chatter from being spoken in Daily sessions.

### 5) README.md

Add a section: "Turn Strategy and Latency Tuning"
- Document every new env var and CLI flag with default values.
- Add migration note: no wake phrase required; command UX remains unchanged.
- Add troubleshooting notes for over-interruption and duplicate command handling.

## Exact New Environment Variables

1. `IP_CONDUCTOR_TURN_PROFILE`
- Values: `fast`, `balanced`, `safe`
- Default: `balanced`
- Purpose: turn sensitivity/debounce posture.

2. `IP_CONDUCTOR_BARGE_IN_MODE`
- Values: `off`, `commands`, `always`
- Default: `commands`
- Purpose: control interruption behavior.

3. `IP_CONDUCTOR_COMMAND_EMIT_SOURCE`
- Values: `interim`, `final`, `turn_stop`
- Default: `turn_stop`
- Purpose: choose when a recognized command is emitted.

4. `IP_CONDUCTOR_IDLE_TIMEOUT_SECONDS`
- Type: integer seconds
- Default: `120`
- Purpose: idle lifecycle threshold.

5. `IP_CONDUCTOR_STT_PROVIDER`
- Values: `deepgram`, `whisper`
- Default: `deepgram` in Daily mode, `whisper` in local mode
- Purpose: STT backend selection.

6. `IP_CONDUCTOR_STT_KEEPALIVE_SECONDS`
- Type: integer seconds
- Default: `20`
- Purpose: STT keepalive interval.

7. `IP_CONDUCTOR_STT_ENDPOINTING_MS`
- Type: integer milliseconds
- Default: `250`
- Purpose: endpointing aggressiveness.

8. `IP_CONDUCTOR_STT_UTTERANCE_END_MS`
- Type: integer milliseconds
- Default: `700`
- Purpose: finalization delay tuning.

9. `IP_CONDUCTOR_TTS_CONCURRENCY`
- Type: integer
- Default: `1`
- Purpose: allow pre-synthesis overlap where supported.

10. `IP_CONDUCTOR_TTS_TEXT_AGGREGATION_MODE`
- Values: `token`, `sentence`
- Default: `sentence`
- Purpose: responsiveness vs coherence tradeoff.

11. `IP_CONDUCTOR_FAILOVER_ENABLED`
- Values: `true`, `false`
- Default: `true`
- Purpose: automatic provider failover.

12. `IP_CONDUCTOR_FAILOVER_CHAIN`
- Type: comma-separated list
- Default: `deepgram,whisper`
- Purpose: fallback order.

13. `IP_CONDUCTOR_METRICS_ENABLED`
- Values: `true`, `false`
- Default: `true`
- Purpose: emit latency/startup/turn metrics.

## Exact New CLI Flags

1. `--turn-profile fast|balanced|safe`
2. `--barge-in-mode off|commands|always`
3. `--command-emit-source interim|final|turn_stop`
4. `--idle-timeout-seconds INTEGER`
5. `--stt-provider deepgram|whisper`
6. `--stt-keepalive-seconds INTEGER`
7. `--stt-endpointing-ms INTEGER`
8. `--stt-utterance-end-ms INTEGER`
9. `--tts-concurrency INTEGER`
10. `--tts-text-aggregation token|sentence`
11. `--failover` and `--no-failover`
12. `--failover-chain CSV`
13. `--metrics` and `--no-metrics`

## Test Checklist

### A) Dependency and Startup
- Install succeeds after Pipecat bump.
- Local mode starts without runtime import errors.
- Daily mode starts with both configured TTS vendors.

### B) Command Correctness
- `next`, `previous`, `first`, `last`, `read`, `pause`, `continue`, `stop`, `highlight`, `delete`, `archive` still work.
- No duplicate execution for one spoken command under noisy/interim/final transcript conditions.
- Delete/archive still enforce protective debounce windows.

### C) Turn Strategy Behavior
- `command-emit-source=interim` yields faster response but potentially noisier triggers.
- `command-emit-source=turn_stop` emits once per turn and suppresses interim/final duplicates.
- `turn-profile` settings produce expected responsiveness and stability tradeoffs.

### D) Barge-In Behavior
- `off`: active sentence playback is not interrupted.
- `commands`: actionable commands interrupt playback.
- `always`: any accepted command interrupts immediately.

### E) Read Pipeline Latency
- Silence gap between sentence N and N+1 decreases after tuning.
- No sentence overlap artifacts at `tts-concurrency=1`.
- If provider supports `tts-concurrency>1`, ordering remains correct.

### F) Failover
- Simulated STT provider failure triggers fallback without process exit.
- Simulated TTS provider failure triggers fallback or graceful speech disablement.

### G) Idle and Shutdown
- Idle events fire at configured threshold.
- Existing Daily empty-room shutdown behavior remains correct in headless mode.

### H) Metrics
- Metrics are emitted when enabled and silent when disabled.
- Spoken output never reads telemetry lines.

### I) Regression
- Keyboard command flow remains unchanged.
- Daily console/chat mirroring still works.
- Highlight capture still targets the intended active utterance.

### J) Documentation
- README examples match implemented flags and env vars.
- README defaults match runtime defaults.

## Recommended Implementation Order

1. Baseline metrics and regression guardrails.
2. Pipecat dependency bump and compatibility pass.
3. Turn/mute strategy integration (no wake phrase).
4. Barge-in policy wiring and command emit source controls.
5. Faster pipeline tuning (STT endpointing/utterance end + optional TTS concurrency path).
6. Service failover and runtime setting updates.
7. README and operator docs update.
