# SPDX-License-Identifier: CC-BY-NC-SA-4.0

"""A simple console application to interact with Instapaper bookmarks."""

import argparse
import os
import sys
import termios
import textwrap
import threading
import time
import tty
import unicodedata
from typing import cast

from dotenv import load_dotenv
from loguru import logger

from article_manager import ArticleManager
from conductor_service import ConductorService
from output_adapter import (
    CompositeOutputAdapter,
    ConsoleOutputAdapter,
    DailyMessageOutputAdapter,
    SpeakingOutputAdapter,
)


def _sanitize_tts_text(text: str) -> str:
    """Remove control/format characters that can break TTS providers."""
    cleaned_chars = []
    for ch in text:
        category = unicodedata.category(ch)
        # Drop control + format characters (e.g. zero-width and soft markers).
        if category in {"Cc", "Cf", "Cs"}:
            continue
        cleaned_chars.append(ch)

    cleaned = "".join(cleaned_chars)
    cleaned = " ".join(cleaned.split())
    return cleaned.strip()


def _maybe_reexec_in_project_venv():
    """Relaunch with the local .venv interpreter when available.

    This keeps runtime behavior aligned with the workspace interpreter even if
    the user starts the app with a different ``python`` on ``PATH``.
    """
    project_python = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python"
    )
    already_reexeced = os.environ.get("IP_CONDUCTOR_VENV_REEXEC") == "1"

    if already_reexeced:
        return

    if not os.path.exists(project_python):
        return

    if os.path.abspath(sys.executable) == os.path.abspath(project_python):
        return

    os.environ["IP_CONDUCTOR_VENV_REEXEC"] = "1"
    print(f"[startup] Switching to project interpreter: {project_python}")
    os.execv(project_python, [project_python, os.path.abspath(__file__), *sys.argv[1:]])


def handle_add_bookmark(service, output):
    """Handle adding a new bookmark."""
    url = input("Enter the URL to bookmark: ").strip()
    if not url:
        output.write_line("No URL entered. Bookmark not added.")
        return
    result = service.add_bookmark(url)
    output.write_lines(result.output_lines)


def handle_delete_bookmark(service, output):
    """Handle deleting the current bookmark."""
    result = service.delete_current_bookmark()
    output.write_lines(result.output_lines)


def handle_star_bookmark(service, output):
    """Handle starring the current bookmark."""
    result = service.star_current_bookmark()
    output.write_lines(result.output_lines)


def handle_create_highlight(service, output):
    """Handle creating a highlight for the current bookmark."""
    manager = service.manager
    info = manager.get_current_bookmark_info()
    if not info:
        output.write_line("No bookmark to create highlight for.")
        return
    title = info[0]
    output.write_line(f"Creating highlight for: {title}")
    output.write_line("Enter the text you want to highlight (press Enter twice to finish):")
    lines = []
    empty_line_count = 0
    while empty_line_count < 2:
        line = input()
        if line.strip() == "":
            empty_line_count += 1
        else:
            empty_line_count = 0
        lines.append(line)
    while lines and lines[-1].strip() == "":
        lines.pop()
    highlight_text = "\n".join(lines).strip()
    if not highlight_text:
        output.write_line("No text entered. Highlight cancelled.")
        return
    result = service.create_highlight_for_current(highlight_text)
    output.write_lines(result.output_lines)


def handle_archive_bookmark(service, output):
    """Handle archiving the current bookmark."""
    result = service.archive_current_bookmark()
    output.write_lines(result.output_lines)


def handle_speak(manager):
    """Handle speak mode - display article sentences one at a time.

    Uses space key to move to next sentence, b to go back, h to highlight
    current sentence, q key to quit.
    """
    print("Parsing article into sentences...")
    sentences = manager.parse_current_article_sentences()
    if not sentences:
        print("No article content available to speak.")
        return

    print(f"\n--- Entering Speak Mode ({len(sentences)} sentences) ---")
    print("Press SPACE for next, B for back, H to highlight, Q to quit\n")

    line_width = int(os.getenv("SPEAK_LINE_WIDTH", "70"))
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    import shutil
    terminal_height = shutil.get_terminal_size().lines

    try:
        tty.setraw(fd)
        sentence_index = 0
        display_sentence = True

        while sentence_index < len(sentences):
            sentence_text = sentences[sentence_index]
            if display_sentence:
                wrapped_lines = textwrap.wrap(sentence_text, width=line_width)
                wrapped_text = "\n\r".join(wrapped_lines)
                sys.stdout.write("\033[2J")
                padding_lines = terminal_height // 2 - 2
                sys.stdout.write("\033[H")
                sys.stdout.write("\n" * padding_lines)
                sys.stdout.write(
                    f"[{sentence_index + 1}/{len(sentences)}]\n\r{wrapped_text}"
                )
                sys.stdout.flush()

            key = sys.stdin.read(1)

            if key.lower() == "q":
                break
            elif key == " ":
                sentence_index += 1
                display_sentence = True
            elif key.lower() == "b":
                if sentence_index > 0:
                    sentence_index -= 1
                display_sentence = True
            elif key.lower() == "h":
                sys.stdout.write("\n\r")
                sys.stdout.flush()
                termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                success, _, _, error = manager.create_highlight_for_current(sentence_text)
                if success:
                    print(
                        f"✓ Highlighted: {sentence_text[:50]}{'...' if len(sentence_text) > 50 else ''}"
                    )
                else:
                    print(f"✗ Error highlighting: {error}")
                tty.setraw(fd)
                display_sentence = False
            else:
                display_sentence = False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        print("\n\n--- Exiting Speak Mode ---")


def handle_speak_auto(
    manager,
    output,
    stop_event,
    pause_event=None,
    voice_listener=None,
    sentence_state=None,
    sentence_state_lock=None,
    console_output=None,
):
    """Voice-driven speak mode that advances sentences automatically.

    When a voice_listener with TTS enabled is supplied, each sentence is spoken
    through the configured TTS pipeline and this loop blocks until that
    utterance has finished before advancing to the next sentence.

    This mode is intended for the voice command flow:
      - "read" starts it
      - "stop" exits it
    """
    tts_active = voice_listener is not None and voice_listener.tts_enabled
    local_output = console_output or output

    def _write_status_line(text: str) -> None:
        # Route status text to the local console path to avoid racey overlap
        # with sentence-tracking utterances, while still mirroring to Daily.
        local_output.write_line(text)
        if (
            local_output is not output
            and voice_listener is not None
            and voice_listener.tts_enabled
        ):
            voice_listener.publish_app_message({"type": "console_line", "text": text})

    _write_status_line("Parsing article into sentences...")
    active_bookmark_url = None
    bookmark_info = manager.get_current_bookmark_info()
    if bookmark_info is not None:
        # Tuple format: (title, url, index, total_count)
        active_bookmark_url = bookmark_info[1]

    sentences = manager.parse_current_article_sentences()
    if not sentences:
        _write_status_line("No article content available to speak.")
        return

    _write_status_line(f"\n--- Entering Speak Mode ({len(sentences)} sentences) ---")
    controls_line = "Voice controls: say 'stop' to exit."
    _write_status_line(f"{controls_line}\n")
    if tts_active and voice_listener is not None:
        # Speak and wait once so read-mode guidance isn't skipped.
        voice_listener.reset_speech_done()
        voice_listener.speak_text(_sanitize_tts_text(controls_line))
        voice_listener.wait_for_speech_done(timeout=3.0)

    sentence_total = len(sentences)

    try:
        sentence_offset = 0
        while sentence_offset < sentence_total:
            if pause_event is not None and pause_event.is_set():
                while pause_event.is_set() and not stop_event.is_set():
                    time.sleep(0.1)

            sentence_index = sentence_offset + 1
            sentence_text = sentences[sentence_offset]
            tts_sentence_text = _sanitize_tts_text(sentence_text)
            if stop_event.is_set():
                break

            if tts_active and voice_listener is not None:
                voice_listener.prepare_utterance_tracking(
                    sentence_text,
                    sentence_index=sentence_index,
                    sentence_total=len(sentences),
                    bookmark_url=active_bookmark_url,
                )
            elif sentence_state is not None and sentence_state_lock is not None:
                with sentence_state_lock:
                    sentence_state["active"] = True
                    sentence_state["text"] = sentence_text
                    sentence_state["index"] = sentence_index
                    sentence_state["total"] = sentence_total
                    sentence_state["bookmark_url"] = active_bookmark_url

            if tts_active and voice_listener is not None:
                voice_listener.reset_speech_done()
                if not tts_sentence_text:
                    output.write_line(
                        f"[tts] Skipping unsupported sentence [{sentence_index}/{sentence_total}]."
                    )
                    sentence_offset += 1
                    continue

                voice_listener.speak_text(tts_sentence_text)
                logger.info("[speak] queued [{}/{}]: {!r}", sentence_index, sentence_total, tts_sentence_text[:80])

                # Wait for THIS sentence to become the active utterance first.
                # Only once it is actually being spoken do we mirror it to chat.
                started = False
                deadline = time.monotonic() + 60.0
                while not stop_event.is_set():
                    if pause_event is not None and pause_event.is_set():
                        logger.info("[speak] paused while waiting for utterance [{}/{}]", sentence_index, sentence_total)
                        break

                    utterance = voice_listener.get_active_utterance()

                    if not started:
                        if utterance is not None and utterance.get("text") == sentence_text:
                            started = True
                            logger.info("[speak] utterance confirmed active [{}/{}]", sentence_index, sentence_total)
                            local_output.write_line(f"[{sentence_index}/{sentence_total}]")
                            local_output.write_line(sentence_text)
                            voice_listener.publish_app_message(
                                {
                                    "type": "console_line",
                                    "text": f"[{sentence_index}/{sentence_total}]",
                                }
                            )
                            voice_listener.publish_app_message(
                                {
                                    "type": "console_line",
                                    "text": sentence_text,
                                }
                            )
                        elif utterance is not None:
                            logger.debug("[speak] waiting for utterance match: got {!r}, want {!r}", (utterance.get("text") or "")[:60], tts_sentence_text[:60])
                    else:
                        if utterance is None:
                            logger.info("[speak] utterance complete [{}/{}], advancing", sentence_index, sentence_total)
                            break

                    time.sleep(0.1)
                    if time.monotonic() >= deadline:
                        logger.warning("[speak] timeout waiting for utterance [{}/{}]; started={}", sentence_index, sentence_total, started)
                        output.write_line("[tts] Speech wait timeout; advancing to next sentence.")
                        break

                should_repeat_current = False
                seek_delta = 0
                if sentence_state is not None and sentence_state_lock is not None:
                    with sentence_state_lock:
                        should_repeat_current = bool(
                            sentence_state.get("repeat_current", False)
                        )
                        sentence_state["repeat_current"] = False
                        seek_delta = int(sentence_state.get("seek_delta", 0) or 0)
                        sentence_state["seek_delta"] = 0

                if should_repeat_current:
                    logger.info("[speak] repeat_current=True for [{}/{}]", sentence_index, sentence_total)
                    continue

                if seek_delta != 0:
                    logger.info("[speak] seek_delta={} from [{}/{}]", seek_delta, sentence_index, sentence_total)
                    sentence_offset = max(0, min(sentence_total - 1, sentence_offset + seek_delta))
                    continue
            else:
                output.write_line(f"[{sentence_index}/{sentence_total}]")
                output.write_line(sentence_text)
                # Minimal fallback pacing if TTS is unavailable.
                for _ in range(10):
                    if stop_event.is_set():
                        break
                    time.sleep(0.1)

            if stop_event.is_set():
                break

            sentence_offset += 1
    finally:
        if sentence_state is not None and sentence_state_lock is not None:
            with sentence_state_lock:
                sentence_state["active"] = False
                sentence_state["text"] = None
                sentence_state["index"] = 0
                sentence_state["total"] = 0
                sentence_state["bookmark_url"] = None
                sentence_state["seek_delta"] = 0
                sentence_state["paused"] = False
        output.write_line("\n--- Exiting Speak Mode ---")


def print_audio_devices():
    """Print available audio devices for voice mode setup."""
    from voice_commands import list_audio_devices

    devices = list_audio_devices()
    if not devices:
        print("No audio devices found.")
        return

    print("Available audio devices:")
    for device in devices:
        max_input_channels = cast(int, device["max_input_channels"])
        max_output_channels = cast(int, device["max_output_channels"])
        input_marker = "input" if max_input_channels > 0 else "-"
        output_marker = "output" if max_output_channels > 0 else "-"
        print(
            f"{device['index']}: {device['name']} "
            f"[{input_marker}, {output_marker}] "
            f"in={max_input_channels} out={max_output_channels}"
        )


def run_console(
    manager,
    voice=False,
    voice_transport="local",
    daily_room_url=None,
    daily_token=None,
    tts_vendor="cartesia",
    headless=False,
    turn_profile=None,
    barge_in_mode=None,
    command_emit_source=None,
    idle_timeout_seconds=None,
    stt_provider=None,
    stt_keepalive_seconds=None,
    stt_endpointing_ms=None,
    stt_utterance_end_ms=None,
    tts_concurrency=None,
    tts_text_aggregation=None,
    failover_enabled=None,
    failover_chain=None,
    metrics_enabled=None,
):
    """Main console interface.

    Args:
        manager: The ArticleManager instance.
        voice: When True, start the pipecat voice command listener so that
               navigation commands can also be issued by speaking into the
               microphone.
        headless: When True, disable keyboard input and keep running until
            the process is stopped externally.
    """
    console_output = ConsoleOutputAdapter()
    output = console_output

    output.write_line("Welcome to the Instapaper Console App!")
    output.write_line(
        "Commands: 'bookmarks' (a), 'add', 'delete' (d), 'star' (s), 'highlight', "
        "'archive' (c), 'speak' (k), 'read' (r), or 'exit'."
    )
    output.write_line("Navigation: 'title', 'next' (n), 'prev' (p), 'first', 'last'")
    output.write_line(
        "With numbers: 'read <number>' (r <number>), 'speak <number>' (k <number>), '<number>'"
    )

    service = ConductorService(manager)
    speak_stop_event = threading.Event()
    speak_pause_event = threading.Event()
    speak_thread = None
    speak_state_lock = threading.Lock()
    current_sentence_state = {
        "active": False,
        "text": None,
        "index": 0,
        "total": 0,
        "bookmark_url": None,
        "repeat_current": False,
        "seek_delta": 0,
        "paused": False,
    }
    current_sentence_lock = threading.Lock()

    def _print_result(result):
        output.write_lines(result.output_lines)

    def _is_speak_running():
        with speak_state_lock:
            return speak_thread is not None and speak_thread.is_alive()

    def _start_voice_speak_mode():
        nonlocal speak_thread

        if _is_speak_running():
            output.write_line("Speak mode is already active.")
            return

        speak_stop_event.clear()
        speak_pause_event.clear()

        with speak_state_lock:
            speak_thread = threading.Thread(
                target=handle_speak_auto,
                args=(
                    manager,
                    output,
                    speak_stop_event,
                    speak_pause_event,
                    voice_listener,
                    current_sentence_state,
                    current_sentence_lock,
                    console_output,
                ),
                daemon=True,
                name="AutoSpeakMode",
            )
            speak_thread.start()

    def _stop_voice_speak_mode():
        nonlocal speak_thread
        speak_stop_event.set()
        speak_pause_event.clear()

        with current_sentence_lock:
            current_sentence_state["paused"] = False

        with speak_state_lock:
            thread = speak_thread

        if thread is not None and thread.is_alive():
            output.write_line("Stopping speak mode...")
            thread.join(timeout=2)

        with speak_state_lock:
            if speak_thread is not None and not speak_thread.is_alive():
                speak_thread = None

    def _is_navigation_mode_command_input(command: str) -> bool:
        lower = command.strip().lower()
        if not lower:
            return False

        if lower in {
            "next",
            "n",
            "previous",
            "prev",
            "p",
            "first",
            "last",
            "title",
            "delete",
            "d",
            "archive",
            "c",
        }:
            return True

        # Numeric jump command (<number>) navigates the bookmark list.
        try:
            int(lower)
            return True
        except ValueError:
            return False

    def _pause_voice_speak_mode():
        if not _is_speak_running():
            output.write_line("[voice] Speak mode is not active.")
            return

        speak_pause_event.set()
        with current_sentence_lock:
            current_sentence_state["paused"] = True
            current_sentence_state["repeat_current"] = True

        if voice_listener is not None and voice_listener.tts_enabled:
            voice_listener.interrupt_tts()

        output.write_line("[voice] Read mode paused.")

    def _continue_voice_speak_mode():
        if _is_speak_running():
            speak_pause_event.clear()
            with current_sentence_lock:
                current_sentence_state["paused"] = False
            output.write_line("[voice] Read mode resumed.")
            return

        output.write_line("[voice] Speak mode is not active; starting read mode.")
        _start_voice_speak_mode()

    def _request_sentence_seek(delta: int) -> bool:
        if not _is_speak_running():
            return False
        if delta == 0:
            return True

        speak_pause_event.clear()
        with current_sentence_lock:
            current_sentence_state["paused"] = False
            current_sentence_state["repeat_current"] = False
            current_sentence_state["seek_delta"] = int(delta)
        return True

    def _request_sentence_repeat() -> bool:
        if not _is_speak_running():
            return False

        speak_pause_event.clear()
        with current_sentence_lock:
            current_sentence_state["paused"] = False
            current_sentence_state["seek_delta"] = 0
            current_sentence_state["repeat_current"] = True
        return True

    def _parse_step_command(command: str, prefix: str) -> int:
        """Parse commands like 'back', 'back 3', 'forward two'."""
        lower = command.strip().lower()
        if lower == prefix:
            return 1

        parts = lower.split(maxsplit=1)
        if len(parts) == 1:
            return 1

        raw_value = parts[1].strip().strip(".,!?")
        if not raw_value:
            return 1

        if raw_value.isdigit():
            parsed = int(raw_value)
            return parsed if parsed > 0 else 1

        number_words = {
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
        return number_words.get(raw_value, 1)

    def _handle_speak_sentence_command(command: str) -> bool:
        lower = command.strip().lower()

        if lower.startswith("back"):
            steps = _parse_step_command(lower, "back")
            if _is_speak_running():
                requested_delta = -steps
                actual_delta = requested_delta
                with current_sentence_lock:
                    current_index = int(current_sentence_state.get("index", 0) or 0)
                    total = int(current_sentence_state.get("total", 0) or 0)

                if current_index > 0 and total > 0:
                    target = max(1, min(total, current_index + requested_delta))
                    actual_delta = target - current_index

                if actual_delta == 0:
                    output.write_line("[voice] Already at the first sentence.")
                    return True

                _request_sentence_seek(actual_delta)
                output.write_line(f"[voice] Back {abs(actual_delta)} sentence(s).")
                return True

            # Outside speak mode, preserve bookmark navigation semantics.
            _print_result(service.execute_command("prev"))
            return True

        if lower.startswith("forward"):
            steps = _parse_step_command(lower, "forward")
            if _is_speak_running():
                requested_delta = steps
                actual_delta = requested_delta
                with current_sentence_lock:
                    current_index = int(current_sentence_state.get("index", 0) or 0)
                    total = int(current_sentence_state.get("total", 0) or 0)

                if current_index > 0 and total > 0:
                    target = max(1, min(total, current_index + requested_delta))
                    actual_delta = target - current_index

                if actual_delta == 0:
                    output.write_line("[voice] Already at the last sentence.")
                    return True

                _request_sentence_seek(actual_delta)
                output.write_line(f"[voice] Forward {abs(actual_delta)} sentence(s).")
                return True

            # Outside speak mode, preserve bookmark navigation semantics.
            _print_result(service.execute_command("next"))
            return True

        if lower in ("repeat", "repeat that"):
            if _request_sentence_repeat():
                output.write_line("[voice] Repeating current sentence.")
                return True
            output.write_line("[voice] Speak mode is not active.")
            return True

        return False

    def _highlight_current_utterance(captured_utterance=None):
        sentence_text = None
        sentence_index = 0
        sentence_total = 0
        bookmark_url = None
        source = "none"

        if captured_utterance is not None:
            sentence_text = captured_utterance.get("text")
            sentence_index = int(captured_utterance.get("index", 0))
            sentence_total = int(captured_utterance.get("total", 0))
            bookmark_url = captured_utterance.get("bookmark_url")
            source = "captured"
        elif voice_listener is not None and voice_listener.tts_enabled:
            utterance = voice_listener.get_current_utterance()
            if utterance is not None:
                sentence_text = utterance.get("text")
                sentence_index = int(utterance.get("index", 0))
                sentence_total = int(utterance.get("total", 0))
                bookmark_url = utterance.get("bookmark_url")
                source = "listener_current"

        if not sentence_text:
            with current_sentence_lock:
                sentence_text = current_sentence_state["text"]
                sentence_index = current_sentence_state["index"]
                sentence_total = current_sentence_state["total"]
                bookmark_url = current_sentence_state.get("bookmark_url")
                if sentence_text:
                    source = "sentence_state"

        if not sentence_text:
            logger.warning("[highlight] no utterance source available")
            output.write_line("No active utterance to highlight.")
            return

        logger.info(
            "[highlight] source={} sentence=[{}/{}] bookmark_url={} text={!r}",
            source,
            sentence_index,
            sentence_total,
            bookmark_url,
            sentence_text[:120],
        )

        if bookmark_url:
            result = service.create_highlight_for_bookmark_url(bookmark_url, sentence_text)
        else:
            # Fallback path for non-speak/manual highlights.
            result = service.create_highlight_for_current(sentence_text)
        # Write detailed result to console only — errors must not reach TTS.
        console_output.write_lines(result.output_lines)
        if result.success:
            output.write_line("Highlight created.")
            if sentence_index and sentence_total:
                console_output.write_line(
                    f"[highlight] Captured utterance [{sentence_index}/{sentence_total}]."
                )
        else:
            output.write_line("Could not create highlight.")

    def _handle_voice_delete() -> None:
        result = service.delete_current_bookmark()
        # Keep detailed lines in console log.
        console_output.write_lines(result.output_lines)

        if result.success:
            # "Article deleted." then the new current title as two separate TTS utterances.
            output.write_line("Article deleted.")
            if len(result.output_lines) >= 2:
                # output_lines[0] = "'title' deleted."
                # output_lines[1..] = new current title lines
                output.write_line(result.output_lines[-1])
        else:
            output.write_line(result.output_lines[0] if result.output_lines else "Delete failed.")

    def _handle_voice_archive() -> None:
        result = service.archive_current_bookmark()
        # Keep detailed lines in console log.
        console_output.write_lines(result.output_lines)

        if result.success:
            output.write_line("Article archived.")
            title_result = service.execute_command("title")
            if title_result.output_lines:
                output.write_line(title_result.output_lines[-1])
        else:
            output.write_line(result.output_lines[0] if result.output_lines else "Archive failed.")

    # ------------------------------------------------------------------
    # Optional voice command listener (pipecat)
    # ------------------------------------------------------------------
    voice_listener = None
    if voice:
        try:
            from voice_commands import VoiceCommandListener, build_voice_pipeline_config

            pipeline_config = build_voice_pipeline_config(
                default_stt_provider=(
                    "deepgram" if voice_transport == "daily" else "whisper"
                ),
                turn_profile=turn_profile,
                barge_in_mode=barge_in_mode,
                command_emit_source=command_emit_source,
                idle_timeout_seconds=idle_timeout_seconds,
                stt_provider=stt_provider,
                stt_keepalive_seconds=stt_keepalive_seconds,
                stt_endpointing_ms=stt_endpointing_ms,
                stt_utterance_end_ms=stt_utterance_end_ms,
                tts_concurrency=tts_concurrency,
                tts_text_aggregation_mode=tts_text_aggregation,
                failover_enabled=failover_enabled,
                failover_chain=tuple(failover_chain) if failover_chain else None,
                metrics_enabled=metrics_enabled,
            )

            def _should_interrupt_for_command(command: str) -> bool:
                if pipeline_config.barge_in_mode == "off":
                    return False
                if pipeline_config.barge_in_mode == "always":
                    return True

                command_root = command.strip().lower().split()[0] if command.strip() else ""
                actionable_commands = {
                    "next",
                    "prev",
                    "previous",
                    "first",
                    "last",
                    "back",
                    "forward",
                    "repeat",
                    "delete",
                    "archive",
                    "read",
                    "pause",
                    "continue",
                    "resume",
                    "stop",
                    "highlight",
                    "mark",
                }
                return command_root in actionable_commands

            def _on_voice_command(command: str) -> None:
                """Called from the pipecat background thread on voice detection."""
                output.write_line(f"\n[voice] {command}")

                if (
                    command != "highlight"
                    and _is_speak_running()
                    and voice_listener is not None
                    and voice_listener.tts_enabled
                    and _should_interrupt_for_command(command)
                ):
                    # Commands in speak mode should stop the current utterance immediately.
                    voice_listener.interrupt_tts()

                if _handle_speak_sentence_command(command):
                    output.write_prompt_hint()
                    return

                if _is_speak_running() and _is_navigation_mode_command_input(command):
                    _stop_voice_speak_mode()

                if command == "delete":
                    _handle_voice_delete()
                    output.write_prompt_hint()
                    return

                if command == "archive":
                    _handle_voice_archive()
                    output.write_prompt_hint()
                    return

                if command == "read":
                    _start_voice_speak_mode()
                    output.write_prompt_hint()
                    return

                if command == "stop":
                    _stop_voice_speak_mode()
                    output.write_prompt_hint()
                    return

                if command == "pause":
                    _pause_voice_speak_mode()
                    output.write_prompt_hint()
                    return

                if command == "continue":
                    _continue_voice_speak_mode()
                    output.write_prompt_hint()
                    return

                if command == "highlight":
                    captured_utterance = None
                    if _is_speak_running() and voice_listener is not None and voice_listener.tts_enabled:
                        captured_utterance = voice_listener.get_current_utterance()
                        with current_sentence_lock:
                            current_sentence_state["repeat_current"] = True
                        voice_listener.interrupt_tts()
                    _highlight_current_utterance(captured_utterance)
                    output.write_prompt_hint()
                    return

                _print_result(service.execute_command(command))
                output.write_prompt_hint()

            voice_listener = VoiceCommandListener(
                on_command=_on_voice_command,
                transport_mode=voice_transport,
                daily_room_url=daily_room_url,
                daily_token=daily_token,
                tts_vendor=tts_vendor,
                cartesia_api_key=os.getenv("CARTESIA_API_KEY"),
                cartesia_voice_id=os.getenv("CARTESIA_VOICE_ID"),
                elevenlabs_api_key=os.getenv("ELEVENLABS_API_KEY"),
                elevenlabs_voice_id=os.getenv("ELEVENLABS_VOICE_ID"),
                shutdown_when_room_empty=(headless and voice_transport == "daily"),
                pipeline_config=pipeline_config,
            )
            output.write_line(
                "[voice] Starting voice command listener "
                f"(transport={voice_transport}; say 'next', 'previous', 'back <#>', 'forward <#>', 'repeat', 'delete', 'archive', 'first', 'last', 'read', 'pause', 'continue', 'highlight', or 'stop')..."
            )
            voice_listener.start()

            if voice_transport == "daily":
                adapters = [
                    ConsoleOutputAdapter(),
                    DailyMessageOutputAdapter(voice_listener.publish_app_message),
                ]
                if voice_listener.tts_enabled:
                    adapters.append(SpeakingOutputAdapter(voice_listener.speak_text))
                    output.write_line(
                        f"[tts] {voice_listener.tts_vendor.title()} TTS enabled — text will be spoken in the room."
                    )
                elif voice_transport == "daily":
                    output.write_line(
                        f"[tts] {voice_listener.tts_vendor.title()} credentials not configured; spoken output disabled."
                    )
                output = CompositeOutputAdapter(adapters)
                output.write_line("[mirror] Daily console mirroring enabled.")

            if voice_transport == "local":
                output.write_line(
                    "[voice] Listening. Whisper model will download on first run (~300 MB)."
                )
            else:
                output.write_line("[voice] Listening via Daily WebRTC + Deepgram.")
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            output.write_line(f"[voice] Could not start voice listener: {exc}")
            voice_listener = None

    # Display the current bookmark title at startup.
    startup_title_result = service.execute_command("title")
    title_tts_listener = (
        voice_listener is not None
        and voice_listener.tts_enabled
        and bool(startup_title_result.output_lines)
    )
    if title_tts_listener and voice_listener is not None:
        voice_listener.reset_speech_done()

    _print_result(startup_title_result)

    if title_tts_listener and voice_listener is not None:
        voice_listener.wait_for_speech_done(timeout=3.0)

    try:
        if headless:
            output.write_line("[headless] Keyboard input disabled.")
            if not voice:
                output.write_line("[headless] Voice mode is disabled; exiting.")
                return
            if voice_listener is None:
                output.write_line("[headless] Voice listener failed to start; exiting.")
                return

            if voice_transport == "daily":
                output.write_line(
                    "[headless] Auto-shutdown is enabled when the Daily room "
                    f"stays empty for {voice_listener.empty_room_shutdown_seconds:.0f}s."
                )

            output.write_line("[headless] Waiting for voice commands...")
            while True:
                if voice_listener.shutdown_requested:
                    reason = voice_listener.shutdown_reason or "Voice listener requested shutdown."
                    output.write_line(f"[headless] {reason}")
                    return

                if not voice_listener.is_running:
                    output.write_line("[headless] Voice listener stopped; exiting.")
                    return

                time.sleep(1)

        while True:
            try:
                cmd = input("> ").strip()

                if _is_speak_running() and voice_listener is not None and voice_listener.tts_enabled and cmd:
                    # Typed commands should also interrupt current utterance immediately.
                    voice_listener.interrupt_tts()

                if _handle_speak_sentence_command(cmd):
                    output.write_prompt_hint()
                    continue

                if _is_speak_running() and _is_navigation_mode_command_input(cmd):
                    _stop_voice_speak_mode()

                result = service.execute_command(cmd)

                if result.action == "add":
                    handle_add_bookmark(service, output)
                elif result.action == "delete":
                    handle_delete_bookmark(service, output)
                elif result.action == "star":
                    handle_star_bookmark(service, output)
                elif result.action == "highlight":
                    if _is_speak_running():
                        _highlight_current_utterance()
                    else:
                        handle_create_highlight(service, output)
                elif result.action == "archive":
                    handle_archive_bookmark(service, output)
                elif result.action == "speak":
                    handle_speak(manager)
                _print_result(result)

                if result.should_exit:
                    break
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except (AttributeError, ValueError, RuntimeError, OSError) as e:
                print(f"An error occurred: {e}")
    finally:
        speak_stop_event.set()
        with speak_state_lock:
            thread = speak_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

        if voice_listener is not None:
            voice_listener.stop()


def main():
    """Main function to run the Instapaper console app."""
    _maybe_reexec_in_project_venv()
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Instapaper console reader with optional voice commands."
    )
    parser.add_argument(
        "--voice",
        action="store_true",
        help=(
            "Enable voice command input. "
            "Use --voice-transport local or --voice-transport daily."
        ),
    )
    parser.add_argument(
        "--voice-transport",
        choices=["local", "daily"],
        default="local",
        help=(
            "Voice transport backend: 'local' (local mic + local Whisper) or "
            "'daily' (Daily WebRTC + Deepgram)."
        ),
    )
    parser.add_argument(
        "--tts-vendor",
        choices=["cartesia", "elevenlabs"],
        default=os.getenv("IP_CONDUCTOR_TTS_VENDOR", "cartesia").lower(),
        help=(
            "TTS vendor for Daily voice output: 'cartesia' or 'elevenlabs'. "
            "Can also be provided via IP_CONDUCTOR_TTS_VENDOR."
        ),
    )
    parser.add_argument(
        "--turn-profile",
        choices=["fast", "balanced", "safe"],
        default=None,
        help=(
            "Turn handling profile for voice command recognition. "
            "Falls back to IP_CONDUCTOR_TURN_PROFILE or 'balanced'."
        ),
    )
    parser.add_argument(
        "--barge-in-mode",
        choices=["off", "commands", "always"],
        default=None,
        help=(
            "Interrupt behavior when voice commands are detected during TTS playback. "
            "Falls back to IP_CONDUCTOR_BARGE_IN_MODE or 'commands'."
        ),
    )
    parser.add_argument(
        "--command-emit-source",
        choices=["interim", "final", "turn_stop"],
        default=None,
        help=(
            "Which transcript stage emits commands. "
            "Falls back to IP_CONDUCTOR_COMMAND_EMIT_SOURCE or 'turn_stop'."
        ),
    )
    parser.add_argument(
        "--idle-timeout-seconds",
        type=int,
        default=None,
        help=(
            "Idle timeout in seconds for voice pipeline behavior. "
            "Falls back to IP_CONDUCTOR_IDLE_TIMEOUT_SECONDS or 120."
        ),
    )
    parser.add_argument(
        "--stt-provider",
        choices=["deepgram", "whisper"],
        default=None,
        help=(
            "STT backend selection. Falls back to IP_CONDUCTOR_STT_PROVIDER "
            "or transport default (deepgram for daily, whisper for local)."
        ),
    )
    parser.add_argument(
        "--stt-keepalive-seconds",
        type=int,
        default=None,
        help=(
            "STT keepalive interval in seconds. "
            "Falls back to IP_CONDUCTOR_STT_KEEPALIVE_SECONDS or 20."
        ),
    )
    parser.add_argument(
        "--stt-endpointing-ms",
        type=int,
        default=None,
        help=(
            "Endpointing aggressiveness in milliseconds. "
            "Falls back to IP_CONDUCTOR_STT_ENDPOINTING_MS or 250."
        ),
    )
    parser.add_argument(
        "--stt-utterance-end-ms",
        type=int,
        default=None,
        help=(
            "Utterance end delay in milliseconds. "
            "Falls back to IP_CONDUCTOR_STT_UTTERANCE_END_MS or 700."
        ),
    )
    parser.add_argument(
        "--tts-concurrency",
        type=int,
        default=None,
        help=(
            "Desired concurrent TTS context count where provider supports it. "
            "Falls back to IP_CONDUCTOR_TTS_CONCURRENCY or 1."
        ),
    )
    parser.add_argument(
        "--tts-text-aggregation",
        choices=["token", "sentence"],
        default=None,
        help=(
            "Text aggregation mode for TTS handling. "
            "Falls back to IP_CONDUCTOR_TTS_TEXT_AGGREGATION_MODE or 'sentence'."
        ),
    )
    parser.add_argument(
        "--failover",
        dest="failover",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable/disable automatic service failover. "
            "Falls back to IP_CONDUCTOR_FAILOVER_ENABLED or true."
        ),
    )
    parser.add_argument(
        "--failover-chain",
        default=None,
        help=(
            "Comma-separated service failover order, e.g. 'deepgram,whisper'. "
            "Falls back to IP_CONDUCTOR_FAILOVER_CHAIN."
        ),
    )
    parser.add_argument(
        "--metrics",
        dest="metrics",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable/disable pipeline metrics logs. "
            "Falls back to IP_CONDUCTOR_METRICS_ENABLED or true."
        ),
    )
    parser.add_argument(
        "--daily-room-url",
        default=os.getenv("DAILY_ROOM_URL"),
        help=(
            "Daily room URL for daily voice transport. "
            "Can also be provided via DAILY_ROOM_URL."
        ),
    )
    parser.add_argument(
        "--daily-token",
        default=os.getenv("DAILY_TOKEN"),
        help=(
            "Daily meeting token for --daily-room-url. "
            "Can also be provided via DAILY_TOKEN."
        ),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help=(
            "Run without interactive keyboard input. "
            "Useful for container deployments where commands are voice-driven."
        ),
    )
    parser.add_argument(
        "--list-audio-devices",
        action="store_true",
        help=(
            "List detected audio devices and exit. Use this to find a microphone "
            "index for IP_CONDUCTOR_INPUT_DEVICE_INDEX."
        ),
    )
    args = parser.parse_args()

    if args.list_audio_devices:
        try:
            print_audio_devices()
        except (ImportError, OSError, RuntimeError, ValueError) as e:
            print(f"Error listing audio devices: {e}")
        return

    try:
        parsed_failover_chain = None
        if args.failover_chain:
            parsed_failover_chain = [
                item.strip().lower()
                for item in args.failover_chain.split(",")
                if item.strip()
            ]

        manager = ArticleManager()
        run_console(
            manager,
            voice=args.voice,
            voice_transport=args.voice_transport,
            daily_room_url=args.daily_room_url,
            daily_token=args.daily_token,
            tts_vendor=args.tts_vendor,
            headless=args.headless,
            turn_profile=args.turn_profile,
            barge_in_mode=args.barge_in_mode,
            command_emit_source=args.command_emit_source,
            idle_timeout_seconds=args.idle_timeout_seconds,
            stt_provider=args.stt_provider,
            stt_keepalive_seconds=args.stt_keepalive_seconds,
            stt_endpointing_ms=args.stt_endpointing_ms,
            stt_utterance_end_ms=args.stt_utterance_end_ms,
            tts_concurrency=args.tts_concurrency,
            tts_text_aggregation=args.tts_text_aggregation,
            failover_enabled=args.failover,
            failover_chain=parsed_failover_chain,
            metrics_enabled=args.metrics,
        )
    except (AttributeError, ValueError, RuntimeError, OSError, KeyError) as e:
        print(f"Error starting application: {e}")
        return
    except KeyboardInterrupt:
        print("\nGoodbye!")
        return


if __name__ == "__main__":
    main()
