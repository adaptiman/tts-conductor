"""Output adapter abstractions for conductor frontends.

The console adapter keeps current terminal behavior intact while allowing future
frontends (e.g. Daily room UI message publisher) to reuse command execution
without being coupled to direct ``print()`` calls.
"""

from typing import Callable, Iterable, Protocol


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
