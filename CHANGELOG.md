# Changelog

All notable changes to this project are documented in this file.

## 0.8.0324.2 - 2026-03-24

### Added
- Added a production deployment runbook section to README.md with manual commands for:
  - updating the repo to a release tag
  - rebuilding and restarting via Docker Compose
  - rebuilding and restarting via plain Docker
  - post-deploy verification via docker ps and logs

### Notes
- This patch release packages documentation-only updates after 0.8.0324.1.

## 0.8.0324.1 - 2026-03-24

### Added
- Added a full implementation plan in PIPECAT_TURN_AND_LATENCY_PLAN.md for turn strategy and latency improvements.
- Added strategy-driven voice pipeline runtime configuration in voice_commands.py with a typed VoicePipelineConfig model.
- Added environment/CLI-resolved runtime knobs for turn profile, barge-in policy, command emit source, STT provider/timing, TTS behavior, failover, and metrics.
- Added strategy-based turn detection wiring with UserTurnProcessor and UserTurnStrategies.
- Added strategy-based user muting with BotSpeakingUserMuteStrategy and FunctionCallUserMuteStrategy integration.
- Added STT failover support through ServiceSwitcher with failover strategy and switch metrics.
- Added PipelineMetricsObserver for lightweight turn/STT/TTS diagnostics.
- Added focused validation script at scripts/check_voice_command_behavior.py.
- Added Daily release QA checklist at docs/voice-daily-validation-checklist.md.
- Added README sections for CLI Reference, Turn Strategy and Latency Tuning, and Daily Validation Runbook.

### Changed
- Upgraded pipecat-ai dependency from 0.0.105 to 0.0.106 in requirements.txt to use newer turn/mute/failover APIs.
- Extended ip_conductor.py CLI with voice pipeline tuning flags:
  - --turn-profile
  - --barge-in-mode
  - --command-emit-source
  - --idle-timeout-seconds
  - --stt-provider
  - --stt-keepalive-seconds
  - --stt-endpointing-ms
  - --stt-utterance-end-ms
  - --tts-concurrency
  - --tts-text-aggregation
  - --failover/--no-failover
  - --failover-chain
  - --metrics/--no-metrics
- Updated run_console in ip_conductor.py to pass typed pipeline configuration into VoiceCommandListener.
- Updated command interruption behavior in ip_conductor.py to respect barge-in policy (off, commands, always).
- Updated output_adapter.py spoken-output filtering to ignore [metrics] and [transport] diagnostic lines.

### Notes
- Wake phrase behavior remains unchanged by design: users continue to issue direct commands (for example: read, pause, next, stop).
- Daily validation should be run using the checklist in docs/voice-daily-validation-checklist.md before production promotion.
