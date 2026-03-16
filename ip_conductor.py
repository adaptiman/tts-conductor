"""A simple console application to interact with Instapaper bookmarks."""

import argparse
import os
import sys
import termios
import threading
import textwrap
import time
import tty
from typing import cast

from article_manager import ArticleManager
from conductor_service import ConductorService
from dotenv import load_dotenv
from output_adapter import (
    CompositeOutputAdapter,
    ConsoleOutputAdapter,
    DailyMessageOutputAdapter,
    SpeakingOutputAdapter,
)


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
    voice_listener=None,
    sentence_state=None,
    sentence_state_lock=None,
    console_output=None,
):
    """Voice-driven speak mode that advances sentences automatically.

    When a voice_listener with TTS enabled is supplied, each sentence is spoken
    through the Cartesia pipeline and this loop blocks until that utterance has
    finished before advancing to the next sentence.

    This mode is intended for the voice command flow:
      - "read" starts it
      - "stop" exits it
    """
    output.write_line("Parsing article into sentences...")
    sentences = manager.parse_current_article_sentences()
    if not sentences:
        output.write_line("No article content available to speak.")
        return

    output.write_line(f"\n--- Entering Speak Mode ({len(sentences)} sentences) ---")
    output.write_line("Voice controls: say 'stop' to exit.\n")

    tts_active = voice_listener is not None and voice_listener.tts_enabled
    local_output = console_output or output
    sentence_total = len(sentences)

    try:
        sentence_offset = 0
        while sentence_offset < sentence_total:
            sentence_index = sentence_offset + 1
            sentence_text = sentences[sentence_offset]
            if stop_event.is_set():
                break

            if tts_active and voice_listener is not None:
                voice_listener.prepare_utterance_tracking(
                    sentence_text,
                    sentence_index=sentence_index,
                    sentence_total=len(sentences),
                )
            elif sentence_state is not None and sentence_state_lock is not None:
                with sentence_state_lock:
                    sentence_state["active"] = True
                    sentence_state["text"] = sentence_text
                    sentence_state["index"] = sentence_index
                    sentence_state["total"] = sentence_total

            if tts_active and voice_listener is not None:
                voice_listener.reset_speech_done()
                voice_listener.speak_text(sentence_text)

                # Wait for THIS sentence to become the active utterance first.
                # Only once it is actually being spoken do we mirror it to chat.
                started = False
                deadline = time.monotonic() + 60.0
                while not stop_event.is_set():
                    utterance = voice_listener.get_active_utterance()

                    if not started:
                        if utterance is not None and utterance.get("text") == sentence_text:
                            started = True
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
                    else:
                        if utterance is None:
                            break

                    time.sleep(0.1)
                    if time.monotonic() >= deadline:
                        output.write_line("[tts] Speech wait timeout; advancing to next sentence.")
                        break

                should_repeat_current = False
                if sentence_state is not None and sentence_state_lock is not None:
                    with sentence_state_lock:
                        should_repeat_current = bool(
                            sentence_state.get("repeat_current", False)
                        )
                        sentence_state["repeat_current"] = False

                if should_repeat_current:
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
):
    """Main console interface.

    Args:
        manager: The ArticleManager instance.
        voice: When True, start the pipecat voice command listener so that
               navigation commands can also be issued by speaking into the
               microphone.
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
    speak_thread = None
    speak_state_lock = threading.Lock()
    current_sentence_state = {
        "active": False,
        "text": None,
        "index": 0,
        "total": 0,
        "repeat_current": False,
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

        with speak_state_lock:
            speak_thread = threading.Thread(
                target=handle_speak_auto,
                args=(
                    manager,
                    output,
                    speak_stop_event,
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
        speak_stop_event.set()

        with speak_state_lock:
            thread = speak_thread

        if thread is not None and thread.is_alive():
            output.write_line("Stopping speak mode...")

    def _highlight_current_utterance():
        sentence_text = None
        sentence_index = 0
        sentence_total = 0

        if voice_listener is not None and voice_listener.tts_enabled:
            utterance = voice_listener.get_current_utterance()
            if utterance is not None:
                sentence_text = utterance.get("text")
                sentence_index = int(utterance.get("index", 0))
                sentence_total = int(utterance.get("total", 0))

        if not sentence_text:
            with current_sentence_lock:
                sentence_text = current_sentence_state["text"]
                sentence_index = current_sentence_state["index"]
                sentence_total = current_sentence_state["total"]

        if not sentence_text:
            output.write_line("No active utterance to highlight.")
            return

        result = service.create_highlight_for_current(sentence_text)
        # Write detailed result to console only — errors must not reach TTS.
        console_output.write_lines(result.output_lines)
        if result.success and sentence_index and sentence_total:
            output.write_line(
                f"[highlight] Captured utterance [{sentence_index}/{sentence_total}]."
            )

    # ------------------------------------------------------------------
    # Optional voice command listener (pipecat)
    # ------------------------------------------------------------------
    voice_listener = None
    if voice:
        try:
            from voice_commands import VoiceCommandListener

            def _on_voice_command(command: str) -> None:
                """Called from the pipecat background thread on voice detection."""
                output.write_line(f"\n[voice] {command}")

                if command == "read":
                    _start_voice_speak_mode()
                    output.write_prompt_hint()
                    return

                if command == "stop":
                    _stop_voice_speak_mode()
                    output.write_prompt_hint()
                    return

                if command == "highlight":
                    if _is_speak_running() and voice_listener is not None and voice_listener.tts_enabled:
                        with current_sentence_lock:
                            current_sentence_state["repeat_current"] = True
                        voice_listener.interrupt_tts()
                    _highlight_current_utterance()
                    output.write_prompt_hint()
                    return

                _print_result(service.execute_command(command))
                output.write_prompt_hint()

            voice_listener = VoiceCommandListener(
                on_command=_on_voice_command,
                transport_mode=voice_transport,
                daily_room_url=daily_room_url,
                daily_token=daily_token,
                cartesia_api_key=os.getenv("CARTESIA_API_KEY"),
                cartesia_voice_id=os.getenv("CARTESIA_VOICE_ID"),
            )
            output.write_line(
                "[voice] Starting voice command listener "
                f"(transport={voice_transport}; say 'next', 'previous', 'first', 'last', 'read', 'highlight', or 'stop')..."
            )
            voice_listener.start()

            if voice_transport == "daily":
                adapters = [
                    ConsoleOutputAdapter(),
                    DailyMessageOutputAdapter(voice_listener.publish_app_message),
                ]
                if voice_listener.tts_enabled:
                    adapters.append(SpeakingOutputAdapter(voice_listener.speak_text))
                    output.write_line("[tts] Cartesia TTS enabled — text will be spoken in the room.")
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

    # Display the current bookmark title at startup
    _print_result(service.execute_command("title"))

    try:
        while True:
            try:
                cmd = input("> ").strip()
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
        manager = ArticleManager()
        run_console(
            manager,
            voice=args.voice,
            voice_transport=args.voice_transport,
            daily_room_url=args.daily_room_url,
            daily_token=args.daily_token,
        )
    except (AttributeError, ValueError, RuntimeError, OSError, KeyError) as e:
        print(f"Error starting application: {e}")
        return
    except KeyboardInterrupt:
        print("\nGoodbye!")
        return


if __name__ == "__main__":
    main()
