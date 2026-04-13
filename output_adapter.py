# SPDX-License-Identifier: CC-BY-NC-SA-4.0

"""Output adapter abstractions for conductor frontends.

The console adapter keeps current terminal behavior intact while allowing future
frontends (e.g. Daily room UI message publisher) to reuse command execution
without being coupled to direct ``print()`` calls.
"""

import re
from typing import Callable, Iterable, Protocol

# Lines that match these patterns carry no useful spoken content.
_TTS_SKIP_RE = re.compile(
    r"^\[(\d+)/(\d+)\]$"          # sentence counter "[N/M]"
    r"|^-{3}"                       # section dividers "---..."
    r"|^\[voice\]"                  # voice-mode status
    r"|^\[mirror\]"                 # mirror status
    r"|^\[tts\]"                    # TTS status
    r"|^\[headless\]"               # headless-mode status
    r"|^\[metrics\]"                # metrics and latency telemetry
    r"|^\[transport\]"              # low-level transport diagnostics
)

# Emoji and pictographic symbols have no spoken equivalent.
_EMOJI_RE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"  # supplementary emoji / symbol / pictograph blocks
    "\U00002600-\U000027BF"  # misc symbols + dingbats (BMP)
    "\U0000FE00-\U0000FE0F"  # variation selectors that affect emoji rendering
    "]+",
    re.UNICODE,
)


class OutputAdapter(Protocol):
    """Interface for presenting conductor output."""

    def write_line(self, text: str = "") -> None:
        """Write a single output line."""

    def write_lines(self, lines: Iterable[str]) -> None:
        """Write multiple output lines in order."""

    def write_prompt_hint(self) -> None:
        """Write a prompt hint suitable for asynchronous notifications."""


class ConsoleOutputAdapter:
    """Terminal-backed output adapter."""

    def write_line(self, text: str = "") -> None:
        print(text)

    def write_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            print(line)

    def write_prompt_hint(self) -> None:
        print("> ", end="", flush=True)


class CompositeOutputAdapter:
    """Fan-out adapter that writes to multiple output adapters."""

    def __init__(self, adapters: list[OutputAdapter]):
        self._adapters = adapters

    def write_line(self, text: str = "") -> None:
        for adapter in self._adapters:
            try:
                adapter.write_line(text)
            except (AttributeError, RuntimeError, OSError, ValueError):
                # Never let secondary outputs break primary console behavior.
                continue

    def write_lines(self, lines: Iterable[str]) -> None:
        materialized = list(lines)
        for adapter in self._adapters:
            try:
                adapter.write_lines(materialized)
            except (AttributeError, RuntimeError, OSError, ValueError):
                continue

    def write_prompt_hint(self) -> None:
        for adapter in self._adapters:
            try:
                adapter.write_prompt_hint()
            except (AttributeError, RuntimeError, OSError, ValueError):
                continue


class DailyMessageOutputAdapter:
    """Adapter that publishes output as structured Daily app messages."""

    def __init__(self, publish_message: Callable[[dict], None]):
        self._publish_message = publish_message

    def write_line(self, text: str = "") -> None:
        try:
            self._publish_message(
                {
                    "type": "console_line",
                    "text": text,
                }
            )
        except (AttributeError, RuntimeError, OSError, ValueError):
            return

    def write_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write_line(line)

    def write_prompt_hint(self) -> None:
        try:
            self._publish_message({"type": "prompt_hint"})
        except (AttributeError, RuntimeError, OSError, ValueError):
            return


class SpeakingOutputAdapter:
    """Adapter that speaks text lines via a TTS callback.

    Lines that are purely UI chrome (sentence counters, section dividers, and
    internal status markers) are silently skipped so only meaningful article
    and command-result text reaches the TTS engine.
    """

    def __init__(self, speak_fn: Callable[[str], None]) -> None:
        self._speak_fn = speak_fn

    def _should_speak(self, text: str) -> bool:
        clean = text.strip()
        return bool(clean) and not _TTS_SKIP_RE.match(clean)

    def write_line(self, text: str = "") -> None:
        if not self._should_speak(text):
            return
        spoken_text = _EMOJI_RE.sub("", " ".join(text.split())).strip()
        if not spoken_text:
            return
        try:
            self._speak_fn(spoken_text)
        except (AttributeError, RuntimeError, OSError, ValueError):
            return

    def write_lines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write_line(line)

    def write_prompt_hint(self) -> None:
        pass  # Prompt hints have no spoken equivalent.
