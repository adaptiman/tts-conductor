"""Core command execution service for the Instapaper conductor app.

This module keeps command parsing and execution separate from terminal I/O so
it can be reused by local console mode and future cloud adapters.
"""

from dataclasses import dataclass, field
import os
import textwrap
from typing import Optional


@dataclass
class CommandResult:
    """Represents the outcome of a command execution."""

    output_lines: list[str] = field(default_factory=list)
    action: Optional[str] = None
    should_exit: bool = False
    success: bool = True


class ConductorService:
    """Command execution service that operates on an ArticleManager instance."""

    def __init__(self, manager):
        self.manager = manager

    def execute_command(self, command: str) -> CommandResult:
        """Parse and execute a command string.

        Returns a ``CommandResult`` containing printable output lines and
        optional action hints for interactive handlers.
        """
        cmd = command.strip()
        cmd_lower = cmd.lower()

        if cmd_lower == "exit":
            return CommandResult(output_lines=["Goodbye!"], should_exit=True)

        if cmd_lower in ("bookmarks", "articles", "a"):
            return CommandResult(output_lines=self._bookmarks_lines())

        if cmd_lower == "add":
            return CommandResult(action="add")

        if cmd_lower in ("delete", "d"):
            return CommandResult(action="delete")

        if cmd_lower in ("star", "s"):
            return CommandResult(action="star")

        if cmd_lower == "highlight":
            return CommandResult(action="highlight")

        if cmd_lower in ("archive", "c"):
            return CommandResult(action="archive")

        if cmd_lower in ("speak", "k"):
            return CommandResult(action="speak")

        if cmd_lower == "title":
            return CommandResult(output_lines=self._title_lines())

        if cmd_lower in ("next", "n"):
            return CommandResult(output_lines=self._navigation_lines("next"))

        if cmd_lower in ("previous", "prev", "p"):
            return CommandResult(output_lines=self._navigation_lines("prev"))

        if cmd_lower == "first":
            return CommandResult(output_lines=self._navigation_lines("first"))

        if cmd_lower == "last":
            return CommandResult(output_lines=self._navigation_lines("last"))

        if cmd_lower in ("read", "r"):
            return CommandResult(output_lines=self._article_lines())

        if cmd_lower.startswith("read ") or cmd_lower.startswith("r "):
            return self._execute_read_number(cmd)

        if cmd_lower.startswith("speak ") or cmd_lower.startswith("k "):
            return self._execute_speak_number(cmd)

        # If input is a number, jump to that article and show title.
        try:
            bookmark_num = int(cmd)
            if self.manager.set_bookmark_by_number(bookmark_num):
                return CommandResult(output_lines=self._title_lines())
            return CommandResult(output_lines=[f"Invalid article number: {bookmark_num}"])
        except ValueError:
            return CommandResult(output_lines=[self._unknown_command_message()])

    def add_bookmark(self, url: str) -> CommandResult:
        """Add a bookmark URL."""
        success, final_url, error = self.manager.add_bookmark_url(url)
        if success:
            return CommandResult(output_lines=[f"Bookmark added successfully: {final_url}"])
        return CommandResult(output_lines=[f"Error adding bookmark: {error}"])

    def delete_current_bookmark(self) -> CommandResult:
        """Delete the currently selected bookmark and return updated title output."""
        info = self.manager.get_current_bookmark_info()
        if not info:
            return CommandResult(output_lines=["No bookmark to delete."])

        success, deleted_title, error = self.manager.delete_current_bookmark()
        if success:
            return CommandResult(
                output_lines=[f"'{deleted_title}' deleted.", *self._title_lines()]
            )
        return CommandResult(output_lines=[f"Error deleting bookmark: {error}"])

    def star_current_bookmark(self) -> CommandResult:
        """Star the currently selected bookmark."""
        success, title, error = self.manager.star_current_bookmark()
        if success:
            return CommandResult(
                output_lines=[f"Bookmark '{title}' starred successfully."]
            )
        return CommandResult(output_lines=[f"Error starring bookmark: {error}"])

    def archive_current_bookmark(self) -> CommandResult:
        """Archive the currently selected bookmark."""
        success, title, error = self.manager.archive_current_bookmark()
        if success:
            return CommandResult(
                output_lines=[f"Bookmark '{title}' archived successfully."]
            )
        return CommandResult(output_lines=[f"Error archiving bookmark: {error}"])

    def create_highlight_for_current(self, highlight_text: str) -> CommandResult:
        """Create a highlight on the currently selected bookmark."""
        info = self.manager.get_current_bookmark_info()
        if not info:
            return CommandResult(output_lines=["No bookmark to create highlight for."])

        success, _title, highlight, error = self.manager.create_highlight_for_current(
            highlight_text
        )
        if success:
            ellipsis = "..." if len(highlight) > 100 else ""
            return CommandResult(
                success=True,
                output_lines=[
                    "Highlight created successfully!",
                    f"Highlighted text: {highlight[:100]}{ellipsis}",
                ]
            )
        return CommandResult(success=False, output_lines=[f"Error creating highlight: {error}"])

    def _execute_read_number(self, cmd: str) -> CommandResult:
        try:
            parts = cmd.split()
            if len(parts) != 2:
                return CommandResult(output_lines=["Usage: read <number> or r <number>"])
            bookmark_num = int(parts[1])
            return CommandResult(output_lines=self._article_lines(bookmark_num))
        except ValueError:
            return CommandResult(
                output_lines=["Invalid bookmark number. Usage: read <number> or r <number>"]
            )

    def _execute_speak_number(self, cmd: str) -> CommandResult:
        try:
            parts = cmd.split()
            if len(parts) != 2:
                return CommandResult(output_lines=["Usage: speak <number> or k <number>"])
            bookmark_num = int(parts[1])
            if self.manager.set_bookmark_by_number(bookmark_num):
                return CommandResult(action="speak")
            return CommandResult(output_lines=[f"Invalid article number: {bookmark_num}"])
        except ValueError:
            return CommandResult(
                output_lines=["Invalid bookmark number. Usage: speak <number> or k <number>"]
            )

    def _bookmarks_lines(self) -> list[str]:
        bookmarks = self.manager.get_bookmarks_list()
        if not bookmarks:
            return ["No bookmarks found."]
        return [f"{i}. {title}" for i, title in enumerate(bookmarks, start=1)]

    def _title_lines(self) -> list[str]:
        title = self.manager.get_current_title()
        if title:
            return [title]
        if self.manager.get_bookmark_count() == 0:
            return ["No bookmarks found."]
        return ["Current index is out of range."]

    def _article_lines(self, bookmark_number: Optional[int] = None) -> list[str]:
        line_width = int(os.getenv("SPEAK_LINE_WIDTH", "70"))

        if bookmark_number is not None:
            if not self.manager.set_bookmark_by_number(bookmark_number):
                return [f"Invalid bookmark number: {bookmark_number}"]

        article = self.manager.get_current_article()
        if article:
            return [textwrap.fill(article, width=line_width)]

        if bookmark_number is not None:
            return [f"Unable to read bookmark {bookmark_number}"]

        if self.manager.get_bookmark_count() == 0:
            return ["No bookmarks found."]

        return ["Current index is out of range."]

    def _navigation_lines(self, direction: str) -> list[str]:
        if direction == "next":
            if self.manager.next_bookmark():
                return self._title_lines()
            return ["Already at the last bookmark."]

        if direction == "prev":
            if self.manager.prev_bookmark():
                return self._title_lines()
            return ["Already at the first bookmark."]

        if direction == "first":
            if self.manager.first_bookmark():
                return self._title_lines()
            return ["No bookmarks found."]

        if direction == "last":
            if self.manager.last_bookmark():
                return self._title_lines()
            return ["No bookmarks found."]

        return [self._unknown_command_message()]

    @staticmethod
    def _unknown_command_message() -> str:
        return (
            "Unknown command. Try: 'bookmarks' (a), 'add', 'delete' (d), 'star' (s), "
            "'highlight', 'archive' (c), 'speak' (k), 'read' (r), 'next' (n), 'prev' (p), "
            "'first', 'last', 'title', '<number>', 'read <number>', 'speak <number>', or 'exit'."
        )
