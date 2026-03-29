---
layout: default
title: Voice Daily Validation Checklist
---

# Voice Daily Validation Checklist

Use this checklist for release sign-off of Daily voice behavior.

## Session Setup

- [ ] Confirm `DAILY_ROOM_URL`, `DAILY_TOKEN`, and `DEEPGRAM_API_KEY` are set.
- [ ] If TTS is required, confirm vendor credentials are set.
- [ ] Start from a clean room with one participant.

## Baseline Launch

Run:

```bash
python ip_conductor.py \
  --voice \
  --voice-transport daily \
  --turn-profile balanced \
  --barge-in-mode commands \
  --command-emit-source turn_stop \
  --metrics
```

- [ ] App starts without tracebacks.
- [ ] Voice listener starts in Daily mode.
- [ ] Metrics lines appear (`[metrics] ...`).

## Command Behavior

Speak this sequence:

1. `read`
2. `pause`
3. `continue`
4. `back two`
5. `forward one`
6. `repeat`
7. `highlight`
8. `stop`

- [ ] Each command triggers once (no duplicate actions).
- [ ] Read flow transitions are correct.
- [ ] Highlight targets the current utterance.

## Safe Profile Regression

Run:

```bash
python ip_conductor.py \
  --voice \
  --voice-transport daily \
  --turn-profile safe \
  --barge-in-mode off \
  --command-emit-source turn_stop \
  --metrics
```

- [ ] Fewer accidental command triggers than balanced profile.
- [ ] Bot speech is not interrupted in `off` mode.

## Failover Drill (Optional but Recommended)

- [ ] Enable `IP_CONDUCTOR_FAILOVER_ENABLED=true`.
- [ ] Set `IP_CONDUCTOR_FAILOVER_CHAIN=deepgram,whisper`.
- [ ] Induce primary STT failure (key/network interruption).
- [ ] Confirm log includes `service.stt_failover_switch`.
- [ ] Confirm session continues and commands recover.

## Log Capture

Run:

```bash
python ip_conductor.py --voice --voice-transport daily --turn-profile balanced --metrics \
  2>&1 | tee /tmp/ip-conductor-daily.log
```

Quick checks:

```bash
grep -E "\[voice\]|\[metrics\]|service\.stt_failover_switch|Invalid RTVI transport message" /tmp/ip-conductor-daily.log
```

- [ ] No repeated malformed RTVI warnings.
- [ ] No unexpected fatal errors.

## RTVI Warning Triage

If repeated `Invalid RTVI transport message` warnings appear:

- [ ] Verify room URL/token are from the same room and token is valid.
- [ ] Re-test with a clean room and only one client.
- [ ] Disable external clients that may send malformed RTVI payloads.
- [ ] Re-run baseline launch and compare logs.

## Sign-off

- [ ] Balanced profile passed.
- [ ] Safe profile passed.
- [ ] Failover drill passed (or explicitly skipped with reason).
- [ ] Logs archived with test date and room identifier.
