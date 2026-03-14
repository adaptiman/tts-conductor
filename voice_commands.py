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
    EndFrame,
    Frame,
    InterimTranscriptionFrame,
    OutputTransportMessageFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.frames.frames import TTSSpeakFrame
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
    "forward": "next",
    "previous": "prev",
    "back": "prev",
    "first": "first",
    "last": "last",
    "read": "read",
    "stop": "stop",
}


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

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            text = frame.text.strip().lower()
            logger.info(f"[VoiceCommands] Transcript: {text!r}")

            # Check each word in the transcript against the command map.
            words = text.split()
            for word in words:
                # Strip common punctuation that Whisper sometimes appends.
                word = word.strip(".,!?")
                if word in _COMMAND_MAP:
                    command = _COMMAND_MAP[word]
                    now = time.monotonic()
                    if now - self._last_command_at < 1.0:
                        break
                    self._last_command_at = now
                    logger.info(f"[VoiceCommands] Command detected: {command!r}")
                    try:
                        self._on_command(command)
                    except (AttributeError, KeyError, OSError, RuntimeError, ValueError) as exc:
                        logger.error(f"[VoiceCommands] on_command raised: {exc}")
                    break  # Only fire one command per utterance

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
        if not self._daily_joined:
            return

        async def _inject() -> None:
            await self._task.queue_frame(TTSSpeakFrame(text=text))

        try:
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
            with self._pending_daily_lock:
                self._pending_daily_messages.clear()

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

        command_processor = VoiceCommandProcessor(on_command=self._on_command)
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
                microphone_out_enabled=False,
            ),
        )
        self._daily_transport = transport

        stt = DeepgramSTTService(
            api_key=self._deepgram_api_key,
        )

        command_processor = VoiceCommandProcessor(on_command=self._on_command)

        if tts_active:
            logger.info(
                f"[TTS] Cartesia TTS enabled (voice={self._cartesia_voice_id!r})"
            )
            tts = CartesiaTTSService(
                api_key=self._cartesia_api_key,
                voice_id=self._cartesia_voice_id,
            )

        @transport.event_handler("on_joined")
        async def on_joined(_transport, _data):
            self._daily_joined = True
            with self._pending_daily_lock:
                pending = list(self._pending_daily_messages)
                self._pending_daily_messages.clear()

            for pending_payload in pending:
                self.publish_app_message(pending_payload)

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
            return Pipeline([transport.input(), stt, command_processor, tts, transport.output()])
