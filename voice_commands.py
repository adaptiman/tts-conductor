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
from typing import Any, Awaitable, Callable, Optional, cast

import aiohttp
from loguru import logger
import pyaudio

# ---------------------------------------------------------------------------
# pipecat imports
# ---------------------------------------------------------------------------
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    EndFrame,
    Frame,
    InterruptionFrame,
    InterimTranscriptionFrame,
    OutputTransportMessageFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.whisper.stt import Model, WhisperSTTService
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
# ---------------------------------------------------------------------------
# Spoken-word → command name map
# ---------------------------------------------------------------------------
_COMMAND_MAP: dict[str, str] = {
    "next": "next",
    "previous": "prev",
    "first": "first",
    "last": "last",
    "delete": "delete",
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

    def __init__(self, on_command: Callable[[str], None], **kwargs):
        super().__init__(**kwargs)
        self._on_command = on_command
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

            def _emit(command: str, transcript: str) -> bool:
                now = time.monotonic()
                # Keep destructive commands on a longer debounce window so a
                # single utterance cannot accidentally trigger twice.
                command_debounce = 3.0 if command == "delete" else 1.0
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
                except Exception as exc:
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
        cartesia_api_key: Optional[str] = None,
        cartesia_voice_id: Optional[str] = None,
    ):
        self._on_command = on_command
        self._model = model
        self._device = device
        self._transport_mode = transport_mode.lower()
        self._daily_room_url = daily_room_url
        self._daily_token = daily_token
        self._daily_api_key = daily_api_key or os.getenv("DAILY_API_KEY")
        self._deepgram_api_key = deepgram_api_key or os.getenv("DEEPGRAM_API_KEY")
        self._cartesia_api_key = cartesia_api_key or os.getenv("CARTESIA_API_KEY")
        self._cartesia_voice_id = cartesia_voice_id or os.getenv("CARTESIA_VOICE_ID")

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
        if self._loop and self._task:
            asyncio.run_coroutine_threadsafe(
                self._task.queue_frame(EndFrame()), self._loop
            )
        if self._loop and self._daily_session:
            asyncio.run_coroutine_threadsafe(self._daily_session.close(), self._loop)
        if self._thread:
            self._thread.join(timeout=10)

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
    def tts_enabled(self) -> bool:
        """True when Cartesia TTS is configured and the transport mode is Daily."""
        return (
            self._transport_mode == "daily"
            and bool(self._cartesia_api_key)
            and bool(self._cartesia_voice_id)
        )

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
    ) -> None:
        """Register the next utterance before it is queued to TTS."""
        with self._utterance_lock:
            self._pending_utterance = {
                "text": text,
                "index": sentence_index,
                "total": sentence_total,
            }

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

    def _on_tts_stopped(self) -> None:
        with self._utterance_lock:
            if self._active_utterance is not None:
                self._last_completed_utterance = dict(self._active_utterance)
                self._last_completed_at = time.monotonic()
            self._active_utterance = None
            self._pending_utterance = None

    def speak_text(self, text: str) -> None:
        """Inject text for immediate TTS synthesis through the Cartesia pipeline.

        The text is wrapped in a :class:`TTSSpeakFrame` and queued DOWNSTREAM
        into the running pipeline where CartesiaTTSService will convert it to
        audio and DailyTransport will broadcast it to room participants.

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

    async def _run_pipeline(self) -> None:
        """Build and run the pipecat pipeline."""
        try:
            if self._transport_mode == "daily":
                pipeline = await self._build_daily_pipeline()
            else:
                pipeline = self._build_local_pipeline()

            self._task = PipelineTask(pipeline)

            # Signal that setup is complete before blocking on runner.run().
            self._ready.set()

            runner = PipelineRunner(handle_sigint=False)
            await runner.run(self._task)
        finally:
            if self._daily_session and not self._daily_session.closed:
                await self._daily_session.close()
            self._daily_transport = None
            self._daily_joined = False
            with self._utterance_lock:
                self._pending_utterance = None
                self._active_utterance = None
                self._last_completed_utterance = None
                self._last_completed_at = 0.0
            with self._pending_daily_lock:
                self._pending_daily_messages.clear()
            with self._pending_tts_lock:
                self._pending_tts_messages.clear()

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

        stt = WhisperSTTService(
            model=self._model,
            device=self._device,
        )

        command_processor = VoiceCommandProcessor(
            on_command=self._on_command,
        )
        # Keep the original working flow for command recognition.
        return Pipeline([transport.input(), stt, command_processor])

    async def _build_daily_pipeline(self) -> Pipeline:
        """Build Daily WebRTC transport + Deepgram STT pipeline."""
        if not self._deepgram_api_key:
            raise RuntimeError(
                "DEEPGRAM_API_KEY is required for Daily + Deepgram mode."
            )

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

        tts_active = bool(self._cartesia_api_key and self._cartesia_voice_id)

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

        stt = DeepgramSTTService(
            api_key=self._deepgram_api_key,
        )

        command_processor = VoiceCommandProcessor(
            on_command=self._on_command,
        )

        if tts_active:
            logger.info(
                f"[TTS] Cartesia TTS enabled (voice={self._cartesia_voice_id!r})"
            )
            tts = CartesiaTTSService(
                api_key=self._cartesia_api_key,
                voice_id=self._cartesia_voice_id,
            )
            self._speech_watcher = SpeechCompletionWatcher(
                on_started=self._on_tts_started,
                on_stopped=self._on_tts_stopped,
            )

        @transport.event_handler("on_joined")
        async def on_joined(_transport, _data):
            self._daily_joined = True
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
            logger.info(
                "[VoiceCommands] Daily participant joined: "
                f"id={participant.get('id', 'unknown')}"
            )

        if tts_active:
            return Pipeline(
                [transport.input(), stt, command_processor, tts, self._speech_watcher, transport.output()]
            )
        return Pipeline([transport.input(), stt, command_processor])
