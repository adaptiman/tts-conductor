# SPDX-License-Identifier: CC-BY-NC-SA-4.0

"""Voice command listener using pipecat-ai for STT-driven navigation.

This module runs a pipecat pipeline in a background thread that listens to
the microphone via Silero VAD and faster-whisper, then fires a callback when
a recognised navigation command is detected.  The console is left completely
untouched - output from the callback (e.g. printing the new article title)
appears in the same terminal window as normal.

Recognised spoken commands
--------------------------
  "next" / "forward"    → "next"
  "previous" / "back"   → "prev"
  "first"               → "first"
  "last"                → "last"

Usage
-----
    listener = VoiceCommandListener(on_command=my_callback)
    listener.start()
    ...
    listener.stop()
"""

import asyncio
import json
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Optional, cast

import aiohttp
import pyaudio
from loguru import logger

# ---------------------------------------------------------------------------
# pipecat imports
# ---------------------------------------------------------------------------
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    StartFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.service_switcher import (
    ServiceSwitcher,
    ServiceSwitcherStrategyFailover,
)
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.whisper.stt import Model, WhisperSTTService
from pipecat.turns.user_mute.base_user_mute_strategy import BaseUserMuteStrategy
from pipecat.turns.user_mute.function_call_user_mute_strategy import (
    FunctionCallUserMuteStrategy,
)
from pipecat.turns.user_start.transcription_user_turn_start_strategy import (
    TranscriptionUserTurnStartStrategy,
)
from pipecat.turns.user_start.vad_user_turn_start_strategy import VADUserTurnStartStrategy
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_turn_processor import UserTurnProcessor
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.transports.local.audio import (
    LocalAudioTransport,
    LocalAudioTransportParams,
)

# ---------------------------------------------------------------------------
# Spoken-word → command name map
# ---------------------------------------------------------------------------
_COMMAND_MAP: dict[str, str] = {
    "next": "next",
    "previous": "prev",
    "first": "first",
    "last": "last",
    "delete": "delete",
    "archive": "archive",
    "highlight": "highlight",
    "mark": "highlight",
    "read": "read",
    "pause": "pause",
    "continue": "continue",
    "resume": "continue",
    "stop": "stop",
}

_NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

_TURN_PROFILES = {"fast", "balanced", "safe"}
_BARGE_IN_MODES = {"off", "commands", "always"}
_COMMAND_EMIT_SOURCES = {"interim", "final", "turn_stop"}
_STT_PROVIDERS = {"deepgram", "whisper"}
_TEXT_AGGREGATION_MODES = {"token", "sentence"}


TurnProfile = Literal["fast", "balanced", "safe"]
BargeInMode = Literal["off", "commands", "always"]
CommandEmitSource = Literal["interim", "final", "turn_stop"]
SttProvider = Literal["deepgram", "whisper"]
TextAggregationMode = Literal["token", "sentence"]


@dataclass(frozen=True)
class VoicePipelineConfig:
    """Runtime knobs for turn detection and latency tuning."""

    turn_profile: TurnProfile = "balanced"
    barge_in_mode: BargeInMode = "commands"
    command_emit_source: CommandEmitSource = "turn_stop"
    idle_timeout_seconds: int = 120
    stt_provider: SttProvider = "deepgram"
    stt_keepalive_seconds: int = 20
    stt_endpointing_ms: int = 250
    stt_utterance_end_ms: int = 700
    tts_concurrency: int = 1
    tts_text_aggregation_mode: TextAggregationMode = "sentence"
    failover_enabled: bool = True
    failover_chain: tuple[str, ...] = ("deepgram", "whisper")
    metrics_enabled: bool = True


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    logger.warning(
        f"[VoiceCommands] Invalid {name}={value!r}; using default={default}."
    )
    return default


def _env_int(name: str, default: int, minimum: Optional[int] = None) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default

    try:
        parsed = int(value)
    except ValueError:
        logger.warning(
            f"[VoiceCommands] Invalid {name}={value!r}; using default={default}."
        )
        return default

    if minimum is not None and parsed < minimum:
        logger.warning(
            f"[VoiceCommands] Invalid {name}={value!r}; minimum is {minimum}. "
            f"Using default={default}."
        )
        return default
    return parsed


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    parsed = tuple(item.strip().lower() for item in value.split(",") if item.strip())
    if not parsed:
        return default
    return parsed


def _parse_choice(
    *,
    value: Optional[str],
    env_name: str,
    default: str,
    allowed: set[str],
) -> str:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in allowed:
        return normalized
    logger.warning(
        f"[VoiceCommands] Invalid {env_name}={value!r}; using default={default}."
    )
    return default


def build_voice_pipeline_config(
    *,
    default_stt_provider: SttProvider,
    turn_profile: Optional[str] = None,
    barge_in_mode: Optional[str] = None,
    command_emit_source: Optional[str] = None,
    idle_timeout_seconds: Optional[int] = None,
    stt_provider: Optional[str] = None,
    stt_keepalive_seconds: Optional[int] = None,
    stt_endpointing_ms: Optional[int] = None,
    stt_utterance_end_ms: Optional[int] = None,
    tts_concurrency: Optional[int] = None,
    tts_text_aggregation_mode: Optional[str] = None,
    failover_enabled: Optional[bool] = None,
    failover_chain: Optional[tuple[str, ...]] = None,
    metrics_enabled: Optional[bool] = None,
) -> VoicePipelineConfig:
    """Build validated runtime config from optional overrides + environment."""
    resolved_turn_profile = _parse_choice(
        value=turn_profile or os.getenv("IP_CONDUCTOR_TURN_PROFILE"),
        env_name="IP_CONDUCTOR_TURN_PROFILE",
        default="balanced",
        allowed=_TURN_PROFILES,
    )
    resolved_barge_in_mode = _parse_choice(
        value=barge_in_mode or os.getenv("IP_CONDUCTOR_BARGE_IN_MODE"),
        env_name="IP_CONDUCTOR_BARGE_IN_MODE",
        default="commands",
        allowed=_BARGE_IN_MODES,
    )
    resolved_emit_source = _parse_choice(
        value=command_emit_source or os.getenv("IP_CONDUCTOR_COMMAND_EMIT_SOURCE"),
        env_name="IP_CONDUCTOR_COMMAND_EMIT_SOURCE",
        default="turn_stop",
        allowed=_COMMAND_EMIT_SOURCES,
    )
    resolved_stt_provider = _parse_choice(
        value=stt_provider or os.getenv("IP_CONDUCTOR_STT_PROVIDER"),
        env_name="IP_CONDUCTOR_STT_PROVIDER",
        default=default_stt_provider,
        allowed=_STT_PROVIDERS,
    )
    resolved_aggregation_mode = _parse_choice(
        value=(
            tts_text_aggregation_mode
            or os.getenv("IP_CONDUCTOR_TTS_TEXT_AGGREGATION_MODE")
        ),
        env_name="IP_CONDUCTOR_TTS_TEXT_AGGREGATION_MODE",
        default="sentence",
        allowed=_TEXT_AGGREGATION_MODES,
    )

    return VoicePipelineConfig(
        turn_profile=cast(TurnProfile, resolved_turn_profile),
        barge_in_mode=cast(BargeInMode, resolved_barge_in_mode),
        command_emit_source=cast(CommandEmitSource, resolved_emit_source),
        idle_timeout_seconds=(
            idle_timeout_seconds
            if idle_timeout_seconds is not None
            else _env_int("IP_CONDUCTOR_IDLE_TIMEOUT_SECONDS", 120, minimum=1)
        ),
        stt_provider=cast(SttProvider, resolved_stt_provider),
        stt_keepalive_seconds=(
            stt_keepalive_seconds
            if stt_keepalive_seconds is not None
            else _env_int("IP_CONDUCTOR_STT_KEEPALIVE_SECONDS", 20, minimum=1)
        ),
        stt_endpointing_ms=(
            stt_endpointing_ms
            if stt_endpointing_ms is not None
            else _env_int("IP_CONDUCTOR_STT_ENDPOINTING_MS", 250, minimum=1)
        ),
        stt_utterance_end_ms=(
            stt_utterance_end_ms
            if stt_utterance_end_ms is not None
            else _env_int("IP_CONDUCTOR_STT_UTTERANCE_END_MS", 700, minimum=1)
        ),
        tts_concurrency=(
            tts_concurrency
            if tts_concurrency is not None
            else _env_int("IP_CONDUCTOR_TTS_CONCURRENCY", 1, minimum=1)
        ),
        tts_text_aggregation_mode=cast(TextAggregationMode, resolved_aggregation_mode),
        failover_enabled=(
            failover_enabled
            if failover_enabled is not None
            else _env_bool("IP_CONDUCTOR_FAILOVER_ENABLED", True)
        ),
        failover_chain=(
            failover_chain
            if failover_chain is not None
            else _env_csv("IP_CONDUCTOR_FAILOVER_CHAIN", ("deepgram", "whisper"))
        ),
        metrics_enabled=(
            metrics_enabled
            if metrics_enabled is not None
            else _env_bool("IP_CONDUCTOR_METRICS_ENABLED", True)
        ),
    )


def _parse_step_count(token: str) -> Optional[int]:
    """Parse an optional step count token used by back/forward commands."""
    cleaned = token.strip().strip(".,!?")
    if not cleaned:
        return None
    if cleaned.isdigit():
        value = int(cleaned)
        return value if value > 0 else None
    return _NUMBER_WORDS.get(cleaned)


def list_audio_devices() -> list[dict[str, object]]:
    """Return available PyAudio devices.

    Each item includes the device index, device name, and input/output channel
    counts. This is used by the CLI to help select a microphone.
    """
    py_audio = pyaudio.PyAudio()
    devices: list[dict[str, object]] = []

    try:
        for index in range(py_audio.get_device_count()):
            info = py_audio.get_device_info_by_index(index)
            devices.append(
                {
                    "index": index,
                    "name": str(info.get("name", f"device-{index}")),
                    "max_input_channels": int(info.get("maxInputChannels", 0)),
                    "max_output_channels": int(info.get("maxOutputChannels", 0)),
                    "default_sample_rate": float(info.get("defaultSampleRate", 0.0)),
                }
            )
    finally:
        py_audio.terminate()

    return devices


class VoiceCommandProcessor(FrameProcessor):
    """Pipecat processor that inspects TranscriptionFrames for navigation commands.

    When a recognised keyword is found in the transcript the ``on_command``
    callback is invoked with the normalised command name ("next", "prev",
    "first", or "last").  All frames are passed through unchanged so the
    pipeline can keep running.

    Args:
        on_command: Callable invoked with the command name string whenever a
            recognised voice command is detected.
    """

    def __init__(
        self,
        on_command: Callable[[str], None],
        command_emit_source: CommandEmitSource = "turn_stop",
        destructive_debounce_seconds: float = 3.0,
        normal_debounce_seconds: float = 1.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._on_command = on_command
        self._command_emit_source = command_emit_source
        self._destructive_debounce_seconds = destructive_debounce_seconds
        self._normal_debounce_seconds = normal_debounce_seconds
        self._last_command_at = 0.0
        self._last_command: Optional[str] = None
        self._last_command_text: Optional[str] = None
        self._last_interim_command_at = 0.0
        self._last_interim_command: Optional[str] = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            is_interim = isinstance(frame, InterimTranscriptionFrame)
            is_final = isinstance(frame, TranscriptionFrame)
            text = frame.text.strip().lower()
            logger.info(f"[VoiceCommands] Transcript: {text!r}")

            def _source_allows_emit() -> bool:
                if self._command_emit_source == "interim":
                    return is_interim
                if self._command_emit_source in {"final", "turn_stop"}:
                    return is_final
                return is_final

            def _emit(command: str, transcript: str) -> bool:
                if not _source_allows_emit():
                    return False

                now = time.monotonic()
                # Keep destructive commands on a longer debounce window so a
                # single utterance cannot accidentally trigger twice.
                command_key = command.split()[0]
                command_debounce = (
                    self._destructive_debounce_seconds
                    if command_key in {"delete", "archive"}
                    else self._normal_debounce_seconds
                )
                if (
                    command == self._last_command
                    and now - self._last_command_at < command_debounce
                ):
                    return False

                # Some STT streams evolve from e.g. "delete" to "delete the"
                # for the same utterance. Treat that expansion as duplicate.
                if (
                    command == self._last_command
                    and self._last_command_text is not None
                    and transcript
                    and transcript != self._last_command_text
                    and now - self._last_command_at < 2.5
                    and (
                        transcript.startswith(self._last_command_text)
                        or self._last_command_text.startswith(transcript)
                    )
                ):
                    return False

                # Interim transcripts often get followed by a final transcript
                # for the same spoken utterance. Suppress that follow-up final.
                if (
                    is_final
                    and self._last_interim_command == command
                    and now - self._last_interim_command_at < 4.0
                ):
                    self._last_interim_command = None
                    self._last_interim_command_at = 0.0
                    return False

                self._last_command_at = now
                self._last_command = command
                self._last_command_text = transcript
                if is_interim:
                    self._last_interim_command = command
                    self._last_interim_command_at = now
                elif is_final:
                    self._last_interim_command = None
                    self._last_interim_command_at = 0.0

                logger.info(f"[VoiceCommands] Command detected: {command!r}")
                try:
                    self._on_command(command)
                except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                    logger.error(f"[VoiceCommands] on_command raised: {exc}")
                return True

            # Check each word in the transcript against the command map.
            words = [w.strip(".,!?") for w in text.split() if w.strip(".,!?")]

            command_emitted = False

            # Sentence-level reading controls with optional numeric count.
            # Examples: "back 3", "forward two", "repeat", "repeat that".
            for index, word in enumerate(words):
                if word == "delete":
                    command_emitted = _emit("delete", text)
                    break

                if word in ("back", "forward"):
                    step = 1
                    if index + 1 < len(words):
                        parsed = _parse_step_count(words[index + 1])
                        if parsed is not None:
                            step = parsed
                    command = f"{word} {step}" if step != 1 else word
                    command_emitted = _emit(command, text)
                    break

                if word == "repeat":
                    command_emitted = _emit("repeat", text)
                    break

            if not command_emitted:
                for word in words:
                    # Strip common punctuation that Whisper sometimes appends.
                    if word in _COMMAND_MAP:
                        command = _COMMAND_MAP[word]
                        _emit(command, text)
                        break  # Only fire one command per utterance

        await self.push_frame(frame, direction)


class SpeechCompletionWatcher(FrameProcessor):
    """Tracks a full speaking cycle and signals completion.

    We only signal completion after seeing BOTH:
      1) ``BotStartedSpeakingFrame``
      2) ``BotStoppedSpeakingFrame``

    This avoids stale/stuck stop frames from a previous utterance making the
    read loop advance too early.
    """

    def __init__(
        self,
        on_started: Optional[Callable[[], None]] = None,
        on_stopped: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._event = threading.Event()
        self._awaiting_cycle = False
        self._started_seen = False
        self._lock = threading.Lock()
        self._on_started = on_started
        self._on_stopped = on_stopped

    def reset(self) -> None:
        """Arm watcher for the next utterance and clear prior signal."""
        with self._lock:
            self._awaiting_cycle = True
            self._started_seen = False
            self._event.clear()

    def wait(self, timeout: float = 60.0) -> bool:
        """Block until speaking stops or timeout expires.

        Returns True if the utterance completed, False on timeout.
        """
        return self._event.wait(timeout=timeout)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if direction == FrameDirection.UPSTREAM:
            with self._lock:
                if self._awaiting_cycle and isinstance(frame, BotStartedSpeakingFrame):
                    self._started_seen = True
                    if self._on_started is not None:
                        self._on_started()
                elif (
                    self._awaiting_cycle
                    and self._started_seen
                    and isinstance(frame, BotStoppedSpeakingFrame)
                ):
                    self._awaiting_cycle = False
                    self._event.set()
                    if self._on_stopped is not None:
                        self._on_stopped()
        await self.push_frame(frame, direction)


class PipelineMetricsObserver(FrameProcessor):
    """Collects lightweight latency and interaction metrics from frames."""

    def __init__(
        self,
        emit_metric: Callable[[str, float, Optional[dict[str, str]]], None],
        enabled: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._emit_metric = emit_metric
        self._enabled = enabled
        self._speech_started_at: Optional[float] = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if not self._enabled:
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InterruptionFrame):
            self._emit_metric("pipeline.interruptions", 1.0, None)

        if isinstance(frame, InterimTranscriptionFrame):
            self._emit_metric(
                "stt.interim_transcript_chars",
                float(len(frame.text.strip())),
                None,
            )

        if isinstance(frame, TranscriptionFrame):
            self._emit_metric(
                "stt.final_transcript_chars",
                float(len(frame.text.strip())),
                None,
            )

        if isinstance(frame, BotStartedSpeakingFrame):
            self._speech_started_at = time.monotonic()
            self._emit_metric("tts.started", 1.0, None)

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._emit_metric("tts.stopped", 1.0, None)
            if self._speech_started_at is not None:
                duration_ms = (time.monotonic() - self._speech_started_at) * 1000
                self._emit_metric("tts.utterance_duration_ms", duration_ms, None)
                self._speech_started_at = None

        await self.push_frame(frame, direction)


class BotSpeakingUserMuteStrategy(BaseUserMuteStrategy):
    """Mute user transcript frames while the bot is actively speaking."""

    def __init__(self):
        super().__init__()
        self._bot_speaking = False

    async def reset(self):
        self._bot_speaking = False

    async def process_frame(self, frame: Frame) -> bool:
        await super().process_frame(frame)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

        return self._bot_speaking


class StrategyUserMuteProcessor(FrameProcessor):
    """Applies user mute strategies and suppresses transcript frames when muted."""

    def __init__(
        self,
        mute_strategies: list[BaseUserMuteStrategy],
        on_mute_state_changed: Optional[Callable[[bool], None]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._mute_strategies = mute_strategies
        self._on_mute_state_changed = on_mute_state_changed
        self._muted = False

    async def _setup_strategies(self) -> None:
        for strategy in self._mute_strategies:
            await strategy.setup(self.task_manager)

    async def _cleanup_strategies(self) -> None:
        for strategy in self._mute_strategies:
            await strategy.cleanup()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self._setup_strategies()
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._cleanup_strategies()

        muted = False
        for strategy in self._mute_strategies:
            if await strategy.process_frame(frame):
                muted = True

        if muted != self._muted:
            self._muted = muted
            if self._on_mute_state_changed is not None:
                self._on_mute_state_changed(muted)

        if self._muted and isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            return

        await self.push_frame(frame, direction)


class VoiceCommandListener:
    """Runs a pipecat STT pipeline in a daemon thread.

    The pipeline is::

        LocalAudioInputTransport (microphone + Silero VAD)
            → WhisperSTTService   (faster-whisper, DISTIL_MEDIUM_EN by default)
            → VoiceCommandProcessor (fires on_command callback)

    Because the pipeline lives in its own thread and event loop it does not
    interfere with the synchronous console loop in ``ip_conductor.py``.

    Args:
        on_command: Callback invoked with a command name string when a voice
            command is recognised.
        model: The faster-whisper model to use.  Defaults to
            ``Model.DISTIL_MEDIUM_EN`` (fast, English-only, ~300 MB download
            on first use).
        device: Inference device passed to faster-whisper ("cpu", "cuda",
            "auto").  Defaults to "cpu".
    """

    def __init__(
        self,
        on_command: Callable[[str], None],
        model: Model = Model.DISTIL_MEDIUM_EN,
        device: str = "cpu",
        transport_mode: str = "local",
        daily_room_url: Optional[str] = None,
        daily_token: Optional[str] = None,
        daily_api_key: Optional[str] = None,
        deepgram_api_key: Optional[str] = None,
        tts_vendor: str = "cartesia",
        cartesia_api_key: Optional[str] = None,
        cartesia_voice_id: Optional[str] = None,
        elevenlabs_api_key: Optional[str] = None,
        elevenlabs_voice_id: Optional[str] = None,
        shutdown_when_room_empty: bool = False,
        empty_room_shutdown_seconds: Optional[float] = None,
        pipeline_config: Optional[VoicePipelineConfig] = None,
    ):
        self._on_command = on_command
        self._model = model
        self._device = device
        self._transport_mode = transport_mode.lower()
        self._tts_vendor = (tts_vendor or "cartesia").strip().lower()
        if self._tts_vendor not in {"cartesia", "elevenlabs"}:
            raise RuntimeError("Unsupported TTS vendor. Use 'cartesia' or 'elevenlabs'.")
        self._daily_room_url = daily_room_url
        self._daily_token = daily_token
        self._daily_api_key = daily_api_key or os.getenv("DAILY_API_KEY")
        self._deepgram_api_key = deepgram_api_key or os.getenv("DEEPGRAM_API_KEY")
        self._cartesia_api_key = cartesia_api_key or os.getenv("CARTESIA_API_KEY")
        self._cartesia_voice_id = cartesia_voice_id or os.getenv("CARTESIA_VOICE_ID")
        self._elevenlabs_api_key = elevenlabs_api_key or os.getenv("ELEVENLABS_API_KEY")
        self._elevenlabs_voice_id = elevenlabs_voice_id or os.getenv("ELEVENLABS_VOICE_ID")
        self._shutdown_when_room_empty = shutdown_when_room_empty
        default_stt_provider: SttProvider = (
            "deepgram" if self._transport_mode == "daily" else "whisper"
        )
        self._pipeline_config = pipeline_config or build_voice_pipeline_config(
            default_stt_provider=default_stt_provider
        )

        if empty_room_shutdown_seconds is None:
            empty_room_shutdown_seconds = 45.0
            timeout_value = os.getenv("EMPTY_ROOM_SHUTDOWN_SECONDS")
            if timeout_value is not None and timeout_value.strip():
                try:
                    empty_room_shutdown_seconds = float(timeout_value)
                except ValueError:
                    logger.warning(
                        "[VoiceCommands] Invalid "
                        f"EMPTY_ROOM_SHUTDOWN_SECONDS={timeout_value!r}; "
                        f"using {empty_room_shutdown_seconds:.0f}s."
                    )

        self._empty_room_shutdown_seconds = max(
            0.0, float(empty_room_shutdown_seconds)
        )

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[PipelineTask] = None
        self._ready = threading.Event()
        self._input_device_index: Optional[int] = None
        self._daily_session: Optional[aiohttp.ClientSession] = None
        self._startup_error: Optional[str] = None
        self._daily_transport: Optional[DailyTransport] = None
        self._daily_joined = False
        self._pending_daily_messages: list[dict] = []
        self._pending_daily_lock = threading.Lock()
        self._pending_tts_messages: list[str] = []
        self._pending_tts_lock = threading.Lock()
        self._speech_watcher: Optional[SpeechCompletionWatcher] = None
        self._utterance_lock = threading.Lock()
        self._pending_utterance: Optional[dict[str, Any]] = None
        self._active_utterance: Optional[dict[str, Any]] = None
        self._last_completed_utterance: Optional[dict[str, Any]] = None
        self._last_completed_at: float = 0.0
        self._shutdown_requested = threading.Event()
        self._shutdown_reason: Optional[str] = None
        self._participant_lock = threading.Lock()
        self._remote_participants: set[str] = set()
        self._had_remote_participant = False
        self._empty_room_shutdown_task: Optional[asyncio.Task] = None
        self._metrics_observer: Optional[PipelineMetricsObserver] = None
        self._user_turn_processor: Optional[UserTurnProcessor] = None
        self._user_mute_processor: Optional[StrategyUserMuteProcessor] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the voice listener in a background daemon thread.

        Returns immediately; the pipeline begins capturing audio once the
        thread has initialised (signalled via an internal threading.Event).
        """
        if self._transport_mode == "local":
            self._input_device_index = self._resolve_input_device_index()
        elif self._transport_mode != "daily":
            raise RuntimeError(
                "Unsupported transport mode. Use 'local' or 'daily'."
            )

        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="VoiceCommandListener",
        )
        self._thread.start()
        # Wait until the pipeline event loop is running before returning.
        if not self._ready.wait(timeout=30):
            raise RuntimeError(
                "Voice listener startup timed out. Check Daily/WebRTC configuration."
            )

        if self._startup_error:
            raise RuntimeError(self._startup_error)

    def stop(self) -> None:
        """Signal the pipeline to stop and wait for the thread to exit."""
        self._shutdown_requested.set()
        self._queue_end_frame()
        if self._loop and self._daily_session:
            try:
                asyncio.run_coroutine_threadsafe(self._daily_session.close(), self._loop)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout=10)

    @property
    def shutdown_requested(self) -> bool:
        """Whether listener requested process shutdown."""
        return self._shutdown_requested.is_set()

    @property
    def shutdown_reason(self) -> Optional[str]:
        """Reason supplied when listener requested shutdown."""
        return self._shutdown_reason

    @property
    def is_running(self) -> bool:
        """Whether the listener thread is currently alive."""
        return bool(self._thread is not None and self._thread.is_alive())

    @property
    def empty_room_shutdown_seconds(self) -> float:
        """Configured empty-room delay before shutdown in Daily mode."""
        return self._empty_room_shutdown_seconds

    def request_shutdown(self, reason: str) -> None:
        """Request graceful pipeline shutdown with a diagnostic reason."""
        if self._shutdown_requested.is_set():
            return

        self._shutdown_reason = reason
        self._shutdown_requested.set()
        logger.info(f"[VoiceCommands] Shutdown requested: {reason}")
        self._queue_end_frame()

    def publish_app_message(self, payload: dict) -> None:
        """Publish a Daily app message when running in Daily transport mode."""
        if not self._loop or not self._daily_transport or self._transport_mode != "daily":
            return

        if not self._daily_joined:
            with self._pending_daily_lock:
                self._pending_daily_messages.append(payload)
            return

        async def _send_message() -> None:
            frame = OutputTransportMessageFrame(json.dumps(payload))

            # Also mirror plain console lines into Daily Prebuilt chat so text
            # is visible in the room UI without a custom viewer page.
            if payload.get("type") == "console_line":
                chat_sender = cast(
                    Optional[Callable[[str, Optional[str]], Awaitable[Any]]],
                    getattr(self._daily_transport, "send_prebuilt_chat_message", None),
                )
                if chat_sender is not None:
                    await chat_sender(str(payload.get("text", "")), "Instapaper Voice Bot")

            # Prefer DailyTransport's native app-message API.
            send_message = cast(
                Optional[Callable[[OutputTransportMessageFrame], Awaitable[Any]]],
                getattr(self._daily_transport, "send_message", None),
            )
            if send_message is not None:
                await send_message(frame)
                return

            # Fallback path if the transport API changes.
            await self._daily_transport.output().queue_frame(frame)

        try:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            # If we are already in the transport loop thread, schedule directly
            # to avoid deadlocking on a blocking wait.
            if running_loop is self._loop:
                self._loop.create_task(_send_message())
                return

            future = asyncio.run_coroutine_threadsafe(_send_message(), self._loop)

            def _on_done(done_future):
                exc = done_future.exception()
                if exc is not None:
                    logger.error(
                        f"[VoiceCommands] Failed to publish Daily app message: {exc!r}"
                    )

            future.add_done_callback(_on_done)
        except RuntimeError:
            # Event loop may be shutting down; ignore late publishes.
            return
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            logger.error(f"[VoiceCommands] Failed to publish Daily app message: {exc!r}")

    @property
    def tts_vendor(self) -> str:
        """Selected TTS vendor for this listener session."""
        return self._tts_vendor

    def _is_tts_vendor_configured(self) -> bool:
        if self._tts_vendor == "elevenlabs":
            return bool(self._elevenlabs_api_key and self._elevenlabs_voice_id)
        return bool(self._cartesia_api_key and self._cartesia_voice_id)

    @property
    def tts_enabled(self) -> bool:
        """True when selected vendor TTS is configured in Daily transport mode."""
        return self._transport_mode == "daily" and self._is_tts_vendor_configured()

    def reset_speech_done(self) -> None:
        """Clear the speech-completion signal before queuing a new utterance."""
        if self._speech_watcher is not None:
            self._speech_watcher.reset()

    def wait_for_speech_done(self, timeout: float = 0.1) -> bool:
        """Wait up to *timeout* seconds for the current utterance to finish.

        Returns True when speaking has stopped, False on timeout.
        Falls back to True immediately if no watcher is available (TTS not active).
        """
        if self._speech_watcher is None:
            return True
        return self._speech_watcher.wait(timeout=timeout)

    def prepare_utterance_tracking(
        self,
        text: str,
        sentence_index: int = 0,
        sentence_total: int = 0,
        bookmark_url: Optional[str] = None,
    ) -> None:
        """Register the next utterance before it is queued to TTS."""
        with self._utterance_lock:
            self._pending_utterance = {
                "text": text,
                "index": sentence_index,
                "total": sentence_total,
                "bookmark_url": bookmark_url,
            }
            logger.info(
                "[utterance] prepared [{}/{}]: {!r}",
                sentence_index,
                sentence_total,
                text[:80],
            )

    def get_current_utterance(self) -> Optional[dict[str, Any]]:
        """Return the best highlight target utterance.

        Preference order:
          1) currently active utterance
          2) just-completed utterance within a short grace window
        """
        with self._utterance_lock:
            if self._active_utterance is not None:
                return dict(self._active_utterance)

            if (
                self._last_completed_utterance is not None
                and time.monotonic() - self._last_completed_at <= 4.0
            ):
                return dict(self._last_completed_utterance)

            return None

    def get_active_utterance(self) -> Optional[dict[str, Any]]:
        """Return only the utterance currently being spoken (no grace window)."""
        with self._utterance_lock:
            if self._active_utterance is None:
                return None
            return dict(self._active_utterance)

    def _on_tts_started(self) -> None:
        with self._utterance_lock:
            if self._pending_utterance is not None:
                self._active_utterance = dict(self._pending_utterance)
                logger.info(
                    "[utterance] started: {!r}",
                    (self._active_utterance.get("text") or "")[:80],
                )
            else:
                logger.warning("[utterance] _on_tts_started fired but _pending_utterance is None")

    def _on_tts_stopped(self) -> None:
        with self._utterance_lock:
            if self._active_utterance is not None:
                logger.info(
                    "[utterance] stopped (normal): {!r}",
                    (self._active_utterance.get("text") or "")[:80],
                )
                self._last_completed_utterance = dict(self._active_utterance)
                self._last_completed_at = time.monotonic()
                self._active_utterance = None
                self._pending_utterance = None
            else:
                # _active_utterance is already None, meaning _clear_utterance_tracking()
                # ran first (interrupt path).  Do NOT clear _pending_utterance here —
                # it may already hold the freshly-registered next sentence, and wiping
                # it would prevent _on_tts_started from promoting it to active.
                logger.info(
                    "[utterance] stopped (post-interrupt): active was None, "
                    "preserving pending={!r}",
                    (self._pending_utterance.get("text") or "")[:80]
                    if self._pending_utterance
                    else None,
                )

    def _clear_utterance_tracking(self) -> None:
        """Clear local utterance state after an intentional interruption.

        In practice the pipeline stop frame can arrive noticeably later than the
        command that requested an interruption. Clearing the local tracking state
        immediately allows read-mode seek/repeat/resume commands to proceed
        without waiting on that delayed callback.
        """
        with self._utterance_lock:
            prev_text = (self._active_utterance.get("text") or "")[:80] if self._active_utterance else None
            if self._active_utterance is not None:
                self._last_completed_utterance = dict(self._active_utterance)
                self._last_completed_at = time.monotonic()
            self._active_utterance = None
            self._pending_utterance = None
            logger.info("[utterance] _clear_utterance_tracking: cleared (was active={!r})", prev_text)

    def speak_text(self, text: str) -> None:
        """Inject text for immediate TTS synthesis through the configured pipeline.

        The text is wrapped in a :class:`TTSSpeakFrame` and queued DOWNSTREAM
        into the running pipeline where the selected TTS service will convert
        it to audio and DailyTransport will broadcast it to room participants.

        This method is thread-safe and non-blocking; it schedules the injection
        onto the pipeline's asyncio event loop and returns immediately.
        """
        if not self.tts_enabled:
            return
        if not self._loop or not self._task:
            return

        # For Daily mode, buffer messages until we've joined the room.
        if self._transport_mode == "daily" and not self._daily_joined:
            with self._pending_tts_lock:
                self._pending_tts_messages.append(text)
            return

        async def _inject() -> None:
            await self._task.queue_frame(TTSSpeakFrame(text=text))

        try:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            # If already on the pipeline loop thread, schedule directly.
            if running_loop is self._loop:
                self._loop.create_task(_inject())
                return

            future = asyncio.run_coroutine_threadsafe(_inject(), self._loop)

            def _on_done(done_future):
                exc = done_future.exception()
                if exc is not None:
                    logger.error(f"[TTS] speak_text failed: {exc!r}")

            future.add_done_callback(_on_done)
        except RuntimeError:
            # Event loop may be shutting down; silently discard late requests.
            pass
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            logger.error(f"[TTS] speak_text error: {exc!r}")

    def interrupt_tts(self) -> None:
        """Interrupt current bot speech playback immediately (if active)."""
        if not self.tts_enabled:
            return
        if not self._loop or not self._task:
            return

        logger.info("[utterance] interrupt_tts called")
        self._clear_utterance_tracking()

        async def _interrupt() -> None:
            await self._task.queue_frame(InterruptionFrame())

        try:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            # If already on the pipeline loop thread, schedule directly.
            if running_loop is self._loop:
                self._loop.create_task(_interrupt())
                return

            future = asyncio.run_coroutine_threadsafe(_interrupt(), self._loop)

            def _on_done(done_future):
                exc = done_future.exception()
                if exc is not None:
                    logger.error(f"[TTS] interrupt_tts failed: {exc!r}")

            future.add_done_callback(_on_done)
        except RuntimeError:
            pass
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            logger.error(f"[TTS] interrupt_tts error: {exc!r}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve_input_device_index(self) -> Optional[int]:
        """Pick a microphone device for PyAudio.

        Resolution order:
        1. `IP_CONDUCTOR_INPUT_DEVICE_INDEX` environment variable
        2. PyAudio default input device
        3. First device with input channels
        """
        requested_index = os.getenv("IP_CONDUCTOR_INPUT_DEVICE_INDEX")
        py_audio = pyaudio.PyAudio()

        try:
            device_count = py_audio.get_device_count()
            input_devices = []
            for index in range(device_count):
                info = py_audio.get_device_info_by_index(index)
                if int(info.get("maxInputChannels", 0)) > 0:
                    input_devices.append((index, str(info.get("name", f"device-{index}"))))

            if requested_index is not None:
                try:
                    selected_index = int(requested_index)
                except ValueError as exc:
                    raise RuntimeError(
                        "IP_CONDUCTOR_INPUT_DEVICE_INDEX must be an integer."
                    ) from exc

                try:
                    info = py_audio.get_device_info_by_index(selected_index)
                except OSError as exc:
                    raise RuntimeError(
                        f"Configured input device index {selected_index} does not exist."
                    ) from exc

                if int(info.get("maxInputChannels", 0)) <= 0:
                    raise RuntimeError(
                        f"Configured device {selected_index} is not an input device."
                    )

                return selected_index

            try:
                default_input = py_audio.get_default_input_device_info()
                return int(default_input["index"])
            except OSError as exc:
                if input_devices:
                    index, name = input_devices[0]
                    logger.info(
                        "[VoiceCommands] No default input device; using first input device "
                        f"#{index} ({name})."
                    )
                    return index

                raise RuntimeError(
                    "No audio input device was found. Connect a microphone or set "
                    "IP_CONDUCTOR_INPUT_DEVICE_INDEX to a valid input device index."
                ) from exc
        finally:
            py_audio.terminate()

    def _run_loop(self) -> None:
        """Entry point for the background thread."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._run_pipeline())
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            self._startup_error = str(exc)
            logger.error(f"[VoiceCommands] Pipeline error: {exc}")
        finally:
            self._ready.set()
            self._loop.close()

    def _queue_end_frame(self) -> None:
        """Queue EndFrame safely from either loop or non-loop threads."""
        if not self._loop or not self._task:
            return

        async def _queue_end() -> None:
            await self._task.queue_frame(EndFrame())

        try:
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None

            if running_loop is self._loop:
                self._loop.create_task(_queue_end())
                return

            asyncio.run_coroutine_threadsafe(_queue_end(), self._loop)
        except RuntimeError:
            # The loop may already be closing; nothing else to do.
            return

    def _participant_id(self, participant: dict[str, Any]) -> str:
        """Return a stable participant identifier when available."""
        for key in ("session_id", "id", "user_id"):
            value = participant.get(key)
            if value:
                return str(value)
        return f"unknown-{time.monotonic_ns()}"

    def _is_remote_participant(self, participant: Any) -> bool:
        if not isinstance(participant, dict):
            return False
        return not bool(participant.get("local"))

    def _mark_participant_joined(self, participant: Any) -> None:
        """Track remote participants and cancel pending empty-room shutdown."""
        if not self._is_remote_participant(participant):
            return

        participant_id = self._participant_id(cast(dict[str, Any], participant))
        with self._participant_lock:
            self._remote_participants.add(participant_id)
            self._had_remote_participant = True
            participant_count = len(self._remote_participants)

        self._cancel_empty_room_shutdown()
        logger.info(
            "[VoiceCommands] Remote participant joined "
            f"(id={participant_id}); active_remote={participant_count}"
        )

    def _mark_participant_left(self, participant: Any) -> None:
        """Track remote departures and schedule room-empty shutdown if needed."""
        if not self._is_remote_participant(participant):
            return

        participant_id = self._participant_id(cast(dict[str, Any], participant))
        with self._participant_lock:
            self._remote_participants.discard(participant_id)
            participant_count = len(self._remote_participants)
            should_schedule_shutdown = (
                self._shutdown_when_room_empty
                and self._had_remote_participant
                and participant_count == 0
            )

        logger.info(
            "[VoiceCommands] Remote participant left "
            f"(id={participant_id}); active_remote={participant_count}"
        )

        if should_schedule_shutdown:
            self._schedule_empty_room_shutdown()

    def _cancel_empty_room_shutdown(self) -> None:
        task = self._empty_room_shutdown_task
        if task is None:
            return

        if not task.done():
            task.cancel()
        self._empty_room_shutdown_task = None

    def _schedule_empty_room_shutdown(self) -> None:
        if not self._shutdown_when_room_empty:
            return

        self._cancel_empty_room_shutdown()

        if self._empty_room_shutdown_seconds <= 0:
            self.request_shutdown("Daily room is empty; shutting down immediately.")
            return

        delay = self._empty_room_shutdown_seconds
        logger.info(
            "[VoiceCommands] Daily room empty; scheduling shutdown in "
            f"{delay:.0f}s unless a participant rejoins."
        )

        async def _shutdown_if_still_empty() -> None:
            try:
                await asyncio.sleep(delay)
                with self._participant_lock:
                    still_empty = len(self._remote_participants) == 0
                if still_empty:
                    self.request_shutdown(
                        "Daily room remained empty; terminating container process."
                    )
            except asyncio.CancelledError:
                return
            finally:
                self._empty_room_shutdown_task = None

        self._empty_room_shutdown_task = asyncio.create_task(_shutdown_if_still_empty())

    async def _run_pipeline(self) -> None:
        """Build and run the pipecat pipeline."""
        try:
            if self._transport_mode == "daily":
                pipeline = await self._build_daily_pipeline()
            else:
                pipeline = self._build_local_pipeline()

            task_kwargs: dict[str, Any] = {}
            if self._transport_mode == "daily":
                # In Daily mode, room idleness is handled by participant-aware
                # shutdown logic. Disable generic pipeline idle cancellation.
                task_kwargs["cancel_on_idle_timeout"] = False
                task_kwargs["idle_timeout_secs"] = None

            self._task = PipelineTask(pipeline, **task_kwargs)

            # Signal that setup is complete before blocking on runner.run().
            self._ready.set()

            runner = PipelineRunner(handle_sigint=False)
            await runner.run(self._task)
        finally:
            if self._daily_session and not self._daily_session.closed:
                await self._daily_session.close()
            self._daily_transport = None
            self._daily_joined = False
            self._cancel_empty_room_shutdown()
            with self._participant_lock:
                self._remote_participants.clear()
                self._had_remote_participant = False
            with self._utterance_lock:
                self._pending_utterance = None
                self._active_utterance = None
                self._last_completed_utterance = None
                self._last_completed_at = 0.0
            with self._pending_daily_lock:
                self._pending_daily_messages.clear()
            with self._pending_tts_lock:
                self._pending_tts_messages.clear()

    def _emit_metric(
        self,
        name: str,
        value: float,
        tags: Optional[dict[str, str]] = None,
    ) -> None:
        """Emit lightweight metrics to logs for tuning and diagnostics."""
        if not self._pipeline_config.metrics_enabled:
            return

        if tags:
            tag_str = ",".join(f"{key}={val}" for key, val in sorted(tags.items()))
            logger.info(f"[metrics] {name}={value:.3f} [{tag_str}]")
            return
        logger.info(f"[metrics] {name}={value:.3f}")

    def _build_stt_service(self):
        """Build STT service based on runtime configuration and transport mode."""
        provider_order = [self._pipeline_config.stt_provider]
        if self._pipeline_config.failover_enabled:
            for provider in self._pipeline_config.failover_chain:
                normalized = provider.strip().lower()
                if normalized in _STT_PROVIDERS and normalized not in provider_order:
                    provider_order.append(cast(SttProvider, normalized))

        services: list[FrameProcessor] = []
        for provider in provider_order:
            service = self._create_stt_service(provider)
            if service is not None:
                services.append(service)

        if not services:
            raise RuntimeError("No usable STT service could be created from configuration.")

        if self._pipeline_config.failover_enabled and len(services) > 1:
            switcher = ServiceSwitcher(
                services=services,
                strategy_type=ServiceSwitcherStrategyFailover,
            )

            @switcher.strategy.event_handler("on_service_switched")
            async def on_service_switched(_strategy, service):
                self._emit_metric(
                    "service.stt_failover_switch",
                    1.0,
                    {"active_service": service.name},
                )
                logger.warning(
                    f"[VoiceCommands] STT service switched to {service.name} after upstream error."
                )

            return switcher

        return services[0]

    def _create_stt_service(self, provider: SttProvider) -> Optional[FrameProcessor]:
        if provider == "deepgram":
            if self._transport_mode != "daily":
                logger.warning(
                    "[VoiceCommands] Deepgram STT is only supported in daily mode; skipping provider."
                )
                return None
            if not self._deepgram_api_key:
                logger.warning(
                    "[VoiceCommands] DEEPGRAM_API_KEY is missing; Deepgram provider unavailable."
                )
                return None
            return DeepgramSTTService(api_key=self._deepgram_api_key)

        return WhisperSTTService(model=self._model, device=self._device)

    def _build_tts_service(self):
        """Build TTS service based on selected vendor and credentials."""
        if not self._is_tts_vendor_configured():
            return None

        if self._tts_vendor == "elevenlabs":
            logger.info(
                f"[TTS] ElevenLabs TTS enabled (voice={self._elevenlabs_voice_id!r})"
            )
            try:
                from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
            except Exception as exc:
                raise RuntimeError(
                    "ElevenLabs TTS is unavailable. Install pipecat-ai[elevenlabs] and ensure ElevenLabs dependencies are present."
                ) from exc

            return ElevenLabsTTSService(
                api_key=cast(str, self._elevenlabs_api_key),
                voice_id=cast(str, self._elevenlabs_voice_id),
            )

        logger.info(f"[TTS] Cartesia TTS enabled (voice={self._cartesia_voice_id!r})")
        return CartesiaTTSService(
            api_key=self._cartesia_api_key,
            voice_id=self._cartesia_voice_id,
        )

    def _build_turn_and_mute_processors(self) -> list[FrameProcessor]:
        """Build strategy-driven turn and mute processors for the pipeline."""
        if self._pipeline_config.turn_profile == "fast":
            speech_timeout = 0.35
            use_interim_start = True
        elif self._pipeline_config.turn_profile == "safe":
            speech_timeout = 0.9
            use_interim_start = False
        else:
            speech_timeout = 0.6
            use_interim_start = True

        user_turn_strategies = UserTurnStrategies(
            start=[
                VADUserTurnStartStrategy(),
                TranscriptionUserTurnStartStrategy(use_interim=use_interim_start),
            ],
            stop=[
                SpeechTimeoutUserTurnStopStrategy(
                    user_speech_timeout=speech_timeout,
                )
            ],
        )

        self._user_turn_processor = UserTurnProcessor(
            user_turn_strategies=user_turn_strategies,
            user_turn_stop_timeout=max(1.0, speech_timeout + 0.6),
            user_idle_timeout=float(self._pipeline_config.idle_timeout_seconds),
        )

        @self._user_turn_processor.event_handler("on_user_turn_started")
        async def on_user_turn_started(_processor, strategy):
            self._emit_metric(
                "turn.user_started",
                1.0,
                {"strategy": strategy.__class__.__name__},
            )

        @self._user_turn_processor.event_handler("on_user_turn_stopped")
        async def on_user_turn_stopped(_processor, strategy):
            self._emit_metric(
                "turn.user_stopped",
                1.0,
                {"strategy": strategy.__class__.__name__},
            )

        @self._user_turn_processor.event_handler("on_user_turn_idle")
        async def on_user_turn_idle(_processor):
            self._emit_metric("turn.user_idle", 1.0, None)

        mute_strategies: list[BaseUserMuteStrategy] = [FunctionCallUserMuteStrategy()]
        if self._pipeline_config.turn_profile == "safe":
            mute_strategies.insert(0, BotSpeakingUserMuteStrategy())

        self._user_mute_processor = StrategyUserMuteProcessor(
            mute_strategies=mute_strategies,
            on_mute_state_changed=lambda muted: self._emit_metric(
                "turn.user_muted", 1.0 if muted else 0.0, None
            ),
        )

        return [self._user_turn_processor, self._user_mute_processor]

    def _build_failover_strategy(self):
        """Return failover strategy class for service switchers when enabled."""
        if self._pipeline_config.failover_enabled:
            return ServiceSwitcherStrategyFailover
        return None

    def _apply_runtime_service_settings(self) -> None:
        """Apply runtime service settings where supported.

        This method intentionally keeps a no-op implementation for now while
        centralizing where typed STT/TTS setting updates will be applied.
        """
        return

    def _build_local_pipeline(self) -> Pipeline:
        """Build local mic + local Whisper pipeline."""
        transport = LocalAudioTransport(
            LocalAudioTransportParams(
                audio_in_enabled=True,
                # Silero VAD segments speech so Whisper only runs on
                # real speech segments, keeping CPU usage low.
                vad_analyzer=SileroVADAnalyzer(),
                input_device_index=self._input_device_index,
            )
        )

        turn_processors = self._build_turn_and_mute_processors()
        stt = self._build_stt_service()
        self._apply_runtime_service_settings()

        command_processor = VoiceCommandProcessor(
            on_command=self._on_command,
            command_emit_source=self._pipeline_config.command_emit_source,
        )
        self._metrics_observer = PipelineMetricsObserver(
            emit_metric=self._emit_metric,
            enabled=self._pipeline_config.metrics_enabled,
        )
        # Keep the original working flow for command recognition.
        return Pipeline(
            [
                transport.input(),
                stt,
                *turn_processors,
                command_processor,
                self._metrics_observer,
            ]
        )

    async def _build_daily_pipeline(self) -> Pipeline:
        """Build Daily WebRTC transport + Deepgram STT pipeline."""
        self._daily_session = aiohttp.ClientSession()

        room_url = self._daily_room_url
        token = self._daily_token

        if not room_url:
            raise RuntimeError(
                "Daily mode requires DAILY_ROOM_URL (or CLI --daily-room-url)."
            )

        if not token:
            logger.info(
                "[VoiceCommands] No DAILY_TOKEN provided; assuming public Daily room."
            )

        print(f"[voice] Daily room: {room_url}")

        tts_active = self._is_tts_vendor_configured()
        turn_processors = self._build_turn_and_mute_processors()

        transport = DailyTransport(
            room_url,
            token,
            "Instapaper Voice Bot",
            DailyParams(
                audio_in_enabled=True,
                audio_out_enabled=tts_active,
                camera_out_enabled=False,
                # microphone_out_enabled controls whether the audio track is
                # actually published in the Daily call. Must be True for
                # participants to hear the TTS output.
                microphone_out_enabled=tts_active,
            ),
        )
        self._daily_transport = transport

        stt = self._build_stt_service()
        self._apply_runtime_service_settings()

        command_processor = VoiceCommandProcessor(
            on_command=self._on_command,
            command_emit_source=self._pipeline_config.command_emit_source,
        )
        self._metrics_observer = PipelineMetricsObserver(
            emit_metric=self._emit_metric,
            enabled=self._pipeline_config.metrics_enabled,
        )

        if tts_active:
            tts = self._build_tts_service()
            if tts is None:
                tts_active = False

        if tts_active:
            self._speech_watcher = SpeechCompletionWatcher(
                on_started=self._on_tts_started,
                on_stopped=self._on_tts_stopped,
            )

        @transport.event_handler("on_joined")
        async def on_joined(_transport, _data):
            self._daily_joined = True
            self._cancel_empty_room_shutdown()
            with self._pending_daily_lock:
                pending = list(self._pending_daily_messages)
                self._pending_daily_messages.clear()
            with self._pending_tts_lock:
                pending_tts = list(self._pending_tts_messages)
                self._pending_tts_messages.clear()

            for pending_payload in pending:
                self.publish_app_message(pending_payload)

            if self._task is not None:
                for pending_text in pending_tts:
                    await self._task.queue_frame(TTSSpeakFrame(text=pending_text))

        @transport.event_handler("on_left")
        async def on_left(_transport):
            self._daily_joined = False

        @transport.event_handler("on_first_participant_joined")
        async def on_first_participant_joined(_transport, participant):
            self._mark_participant_joined(participant)

        @transport.event_handler("on_participant_joined")
        async def on_participant_joined(_transport, participant):
            self._mark_participant_joined(participant)

        @transport.event_handler("on_participant_left")
        async def on_participant_left(_transport, participant, _reason=None):
            self._mark_participant_left(participant)

        if tts_active:
            return Pipeline(
                [
                    transport.input(),
                    stt,
                    *turn_processors,
                    command_processor,
                    self._metrics_observer,
                    tts,
                    self._speech_watcher,
                    transport.output(),
                ]
            )
        return Pipeline(
            [
                transport.input(),
                stt,
                *turn_processors,
                command_processor,
                self._metrics_observer,
            ]
        )
