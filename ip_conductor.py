"""A simple console application to interact with Instapaper bookmarks."""

import argparse
import os
import sys
import termios
import textwrap
import time
import tty

from article_manager import ArticleManager
from dotenv import load_dotenv


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


def slow_print(text, delay=0.05):
    """Prints strings slowly to the console."""
    for char in text:
        print(char, end="", flush=True)
        time.sleep(delay)
    print()


def display_title(manager):
    """Display the current bookmark title."""
    title = manager.get_current_title()
    if title:
        print(title)
    else:
        if manager.get_bookmark_count() == 0:
            print("No bookmarks found.")
        else:
            print("Current index is out of range.")


def display_article(manager, bookmark_number=None):
    """Display the bookmark content with word wrapping."""
    line_width = int(os.getenv("SPEAK_LINE_WIDTH", "70"))
    if bookmark_number is not None:
        if manager.set_bookmark_by_number(bookmark_number):
            article = manager.get_current_article()
            if article:
                print(textwrap.fill(article, width=line_width))
            else:
                print(f"Unable to read bookmark {bookmark_number}")
        else:
            print(f"Invalid bookmark number: {bookmark_number}")
    else:
        article = manager.get_current_article()
        if article:
            print(textwrap.fill(article, width=line_width))
        else:
            if manager.get_bookmark_count() == 0:
                print("No bookmarks found.")
            else:
                print("Current index is out of range.")


def display_bookmarks(manager):
    """Display all bookmarks with numbers."""
    bookmarks = manager.get_bookmarks_list()
    if not bookmarks:
        print("No bookmarks found.")
    else:
        for i, title in enumerate(bookmarks, start=1):
            print(f"{i}. {title}")


def handle_add_bookmark(manager):
    """Handle adding a new bookmark."""
    url = input("Enter the URL to bookmark: ").strip()
    if not url:
        print("No URL entered. Bookmark not added.")
        return
    success, url, error = manager.add_bookmark_url(url)
    if success:
        print(f"Bookmark added successfully: {url}")
    else:
        print(f"Error adding bookmark: {error}")


def handle_delete_bookmark(manager):
    """Handle deleting the current bookmark."""
    info = manager.get_current_bookmark_info()
    if not info:
        print("No bookmark to delete.")
        return
    success, deleted_title, error = manager.delete_current_bookmark()
    if success:
        print(f"'{deleted_title}' deleted.")
        display_title(manager)
    else:
        print(f"Error deleting bookmark: {error}")


def handle_star_bookmark(manager):
    """Handle starring the current bookmark."""
    success, title, error = manager.star_current_bookmark()
    if success:
        print(f"Bookmark '{title}' starred successfully.")
    else:
        print(f"Error starring bookmark: {error}")


def handle_create_highlight(manager):
    """Handle creating a highlight for the current bookmark."""
    info = manager.get_current_bookmark_info()
    if not info:
        print("No bookmark to create highlight for.")
        return
    title = info[0]
    print(f"Creating highlight for: {title}")
    print("Enter the text you want to highlight (press Enter twice to finish):")
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
        print("No text entered. Highlight cancelled.")
        return
    success, title, highlight, error = manager.create_highlight_for_current(highlight_text)
    if success:
        print("Highlight created successfully!")
        ellipsis = "..." if len(highlight) > 100 else ""
        print(f"Highlighted text: {highlight[:100]}{ellipsis}")
    else:
        print(f"Error creating highlight: {error}")


def handle_archive_bookmark(manager):
    """Handle archiving the current bookmark."""
    success, title, error = manager.archive_current_bookmark()
    if success:
        print(f"Bookmark '{title}' archived successfully.")
    else:
        print(f"Error archiving bookmark: {error}")


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


def handle_navigation(manager, direction):
    """Handle navigation commands."""
    if direction == "next":
        if manager.next_bookmark():
            display_title(manager)
        else:
            print("Already at the last bookmark.")
    elif direction == "prev":
        if manager.prev_bookmark():
            display_title(manager)
        else:
            print("Already at the first bookmark.")
    elif direction == "first":
        if manager.first_bookmark():
            display_title(manager)
        else:
            print("No bookmarks found.")
    elif direction == "last":
        if manager.last_bookmark():
            display_title(manager)
        else:
            print("No bookmarks found.")


def print_audio_devices():
    """Print available audio devices for voice mode setup."""
    from voice_commands import list_audio_devices

    devices = list_audio_devices()
    if not devices:
        print("No audio devices found.")
        return

    print("Available audio devices:")
    for device in devices:
        input_marker = "input" if device["max_input_channels"] > 0 else "-"
        output_marker = "output" if device["max_output_channels"] > 0 else "-"
        print(
            f"{device['index']}: {device['name']} "
            f"[{input_marker}, {output_marker}] "
            f"in={device['max_input_channels']} out={device['max_output_channels']}"
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
    print("Welcome to the Instapaper Console App!")
    print(
        "Commands: 'bookmarks' (a), 'add', 'delete' (d), 'star' (s), 'highlight', "
        "'archive' (c), 'speak' (k), 'read' (r), or 'exit'."
    )
    print("Navigation: 'title', 'next' (n), 'prev' (p), 'first', 'last'")
    print(
        "With numbers: 'read <number>' (r <number>), 'speak <number>' (k <number>), '<number>'"
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
                print(f"\n[voice] {command}")
                handle_navigation(manager, command)
                print("> ", end="", flush=True)

            voice_listener = VoiceCommandListener(
                on_command=_on_voice_command,
                transport_mode=voice_transport,
                daily_room_url=daily_room_url,
                daily_token=daily_token,
            )
            print(
                "[voice] Starting voice command listener "
                f"(transport={voice_transport}; say 'next', 'previous', 'first', or 'last')..."
            )
            voice_listener.start()
            if voice_transport == "local":
                print(
                    "[voice] Listening. Whisper model will download on first run (~300 MB)."
                )
            else:
                print("[voice] Listening via Daily WebRTC + Deepgram.")
        except (ImportError, RuntimeError, OSError, ValueError) as exc:
            print(f"[voice] Could not start voice listener: {exc}")
            voice_listener = None

    # Display the current bookmark title at startup
    display_title(manager)

    try:
        while True:
            try:
                cmd = input("> ").strip()
                cmd_lower = cmd.lower()

                if cmd_lower == "exit":
                    print("Goodbye!")
                    break
                elif cmd_lower in ("bookmarks", "articles", "a"):
                    display_bookmarks(manager)
                elif cmd_lower == "add":
                    handle_add_bookmark(manager)
                elif cmd_lower in ("delete", "d"):
                    handle_delete_bookmark(manager)
                elif cmd_lower in ("star", "s"):
                    handle_star_bookmark(manager)
                elif cmd_lower == "highlight":
                    handle_create_highlight(manager)
                elif cmd_lower in ("archive", "c"):
                    handle_archive_bookmark(manager)
                elif cmd_lower in ("speak", "k"):
                    handle_speak(manager)
                elif cmd_lower == "title":
                    display_title(manager)
                elif cmd_lower in ("next", "n"):
                    handle_navigation(manager, "next")
                elif cmd_lower in ("previous", "prev", "p"):
                    handle_navigation(manager, "prev")
                elif cmd_lower == "first":
                    handle_navigation(manager, "first")
                elif cmd_lower == "last":
                    handle_navigation(manager, "last")
                elif cmd_lower in ("read", "r"):
                    display_article(manager)
                elif cmd_lower.startswith("read ") or cmd_lower.startswith("r "):
                    try:
                        parts = cmd.split()
                        if len(parts) == 2:
                            display_article(manager, int(parts[1]))
                        else:
                            print("Usage: read <number> or r <number>")
                    except ValueError:
                        print("Invalid bookmark number. Usage: read <number> or r <number>")
                elif cmd_lower.startswith("speak ") or cmd_lower.startswith("k "):
                    try:
                        parts = cmd.split()
                        if len(parts) == 2:
                            bookmark_num = int(parts[1])
                            if manager.set_bookmark_by_number(bookmark_num):
                                handle_speak(manager)
                            else:
                                print(f"Invalid article number: {bookmark_num}")
                        else:
                            print("Usage: speak <number> or k <number>")
                    except ValueError:
                        print("Invalid bookmark number. Usage: speak <number> or k <number>")
                else:
                    try:
                        bookmark_num = int(cmd)
                        if manager.set_bookmark_by_number(bookmark_num):
                            display_title(manager)
                        else:
                            print(f"Invalid article number: {bookmark_num}")
                    except ValueError:
                        print(
                            "Unknown command. Try: 'bookmarks' (a), 'add', 'delete' (d), 'star' (s), "
                            "'highlight', 'archive' (c), 'speak' (k), 'read' (r), 'next' (n), 'prev' (p), "
                            "'first', 'last', 'title', '<number>', 'read <number>', 'speak <number>', or 'exit'."
                        )
            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except (AttributeError, ValueError, RuntimeError, OSError) as e:
                print(f"An error occurred: {e}")
    finally:
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
