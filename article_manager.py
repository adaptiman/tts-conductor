# SPDX-License-Identifier: CC-BY-NC-SA-4.0

"""ArticleManager class for managing Instapaper bookmark operations and navigation."""

import os

import instapaper
import spacy
from dotenv import load_dotenv


class ArticleManager:
    """Manages Instapaper bookmark operations and navigation."""

    def __init__(self, bookmark_limit=25):
        """Initialize the ArticleManager with Instapaper API connection."""
        self.bookmark_limit = bookmark_limit
        self.current_index = 0
        self.instapaper_client = None
        self._nlp = None  # Lazy load spaCy model
        self._initialize_client()

    def _initialize_client(self):
        """Initialize the Instapaper client with credentials from .env file."""
        # Load environment variables from .env file
        load_dotenv()

        try:
            # Get credentials from environment variables
            login = os.getenv("INSTAPAPER_USERNAME")
            password = os.getenv("INSTAPAPER_PASSWORD")
            consumerkey = os.getenv("INSTAPAPER_CONSUMER_KEY")
            consumersecret = os.getenv("INSTAPAPER_CONSUMER_SECRET")

            # Validate that all credentials are present
            if not all([login, password, consumerkey, consumersecret]):
                missing = []
                if not login:
                    missing.append("INSTAPAPER_USERNAME")
                if not password:
                    missing.append("INSTAPAPER_PASSWORD")
                if not consumerkey:
                    missing.append("INSTAPAPER_CONSUMER_KEY")
                if not consumersecret:
                    missing.append("INSTAPAPER_CONSUMER_SECRET")
                raise ValueError(
                    f"Missing required environment variables: {', '.join(missing)}"
                )

            self.instapaper_client = instapaper.Instapaper(consumerkey, consumersecret)
            self.instapaper_client.login(login, password)
        except (
            AttributeError,
            ValueError,
            RuntimeError,
            OSError,
            KeyError,
        ) as e:
            raise RuntimeError(f"Error initializing Instapaper client: {e}") from e

    def _get_bookmarks(self):
        """Get bookmarks with error handling."""
        if self.instapaper_client is None:
            return None
        try:
            return self.instapaper_client.bookmarks(limit=self.bookmark_limit)
        except (AttributeError, ValueError, RuntimeError, OSError):
            return None

    def get_current_title(self):
        """Gets the current bookmark title."""
        marks = self._get_bookmarks()
        if not marks:
            return None

        if 0 <= self.current_index < len(marks):
            m = marks[self.current_index]
            return str(m.title)
        return None

    def get_current_article(self):
        """Gets the content of the current bookmark."""
        marks = self._get_bookmarks()
        if not marks:
            return None

        if 0 <= self.current_index < len(marks):
            m = marks[self.current_index]
            return str(m.text)
        else:
            return None

    def next_bookmark(self):
        """Navigates to the next bookmark."""
        marks = self._get_bookmarks()
        if marks and self.current_index < len(marks) - 1:
            self.current_index += 1
            return True
        return False

    def prev_bookmark(self):
        """Navigates to the previous bookmark."""
        if self.current_index > 0:
            self.current_index -= 1
            return True
        return False

    def first_bookmark(self):
        """Navigates to the first bookmark."""
        marks = self._get_bookmarks()
        if marks:
            self.current_index = 0
            return True
        return False

    def last_bookmark(self):
        """Navigates to the last bookmark."""
        marks = self._get_bookmarks()
        if marks:
            self.current_index = len(marks) - 1
            return True
        return False

    def get_bookmarks_list(self):
        """Gets a list of all bookmark titles."""
        marks = self._get_bookmarks()
        if not marks:
            return []
        return [m.title for m in marks]

    def delete_current_bookmark(self):
        """Deletes the currently selected bookmark. Returns (success, title, error_msg)."""
        marks = self._get_bookmarks()
        if not marks:
            return (False, None, "No bookmarks found")

        if 0 <= self.current_index < len(marks):
            m = marks[self.current_index]
            title = m.title
            try:
                m.delete()
                return (True, title, None)
            except (AttributeError, ValueError, RuntimeError, OSError) as e:
                return (False, title, str(e))
        else:
            return (False, None, "Current index is out of range")

    def star_current_bookmark(self):
        """Stars the currently selected bookmark. Returns (success, title, error_msg)."""
        marks = self._get_bookmarks()
        if not marks:
            return (False, None, "No bookmarks found")

        if 0 <= self.current_index < len(marks):
            m = marks[self.current_index]
            title = m.title
            try:
                m.star()
                return (True, title, None)
            except (AttributeError, ValueError, RuntimeError, OSError) as e:
                return (False, title, str(e))
        else:
            return (False, None, "Current index is out of range")

    def add_bookmark_url(self, url):
        """Adds a new bookmark. Returns (success, url, error_msg)."""
        if not url or not url.strip():
            return (False, url, "No URL provided")

        url = url.strip()
        try:
            # Create a new Bookmark object and save it
            # The instapaper library requires creating a Bookmark instance
            # with the parent client and params, then calling save()
            bookmark = instapaper.Bookmark(self.instapaper_client, {"url": url})
            bookmark.save()
            return (True, url, None)
        except (AttributeError, ValueError, RuntimeError, OSError) as e:
            return (False, url, str(e))

    def _highlight_occurrence_index(self, article_text, highlight_text, char_offset):
        """Convert a character offset into Instapaper's occurrence index."""
        if not article_text or not highlight_text:
            return None

        if not isinstance(char_offset, int) or char_offset < 0:
            return None

        highlight_end = char_offset + len(highlight_text)
        if article_text[char_offset:highlight_end] != highlight_text:
            return None

        # Instapaper's `position` is the count of prior matching selections,
        # not the raw character offset in the article body.
        return article_text[:char_offset].count(highlight_text)

    def create_highlight_for_current(self, highlight_text, position=0):
        """Creates a highlight for the current bookmark.

        Args:
            highlight_text: The text to highlight.
            position: Optional. A character offset used to identify which repeated
                instance of the selected text should be highlighted.

        Returns (success, title, highlight_text, error_msg).
        """
        marks = self._get_bookmarks()
        if not marks:
            return (False, None, highlight_text, "No bookmarks found")

        if 0 <= self.current_index < len(marks):
            m = marks[self.current_index]
            title = m.title

            if not highlight_text or not highlight_text.strip():
                return (False, title, highlight_text, "No text provided for highlight")

            highlight_text = highlight_text.strip()

            requested_position = None
            if isinstance(position, int) and position >= 0:
                requested_position = position

            # Instapaper can be strict about exact text/position matching.
            # Try a small set of variants and prefer position-aware requests
            # when we can locate the text in the article body.
            article_text = str(getattr(m, "text", "") or "")
            normalized_text = " ".join(highlight_text.split())
            candidates = [highlight_text]
            if normalized_text and normalized_text != highlight_text:
                candidates.append(normalized_text)

            last_error = None
            for candidate in candidates:
                try:
                    if requested_position is not None and article_text:
                        occurrence_index = self._highlight_occurrence_index(
                            article_text,
                            candidate,
                            requested_position,
                        )
                        if occurrence_index is not None:
                            m.create_highlight(candidate, position=occurrence_index)
                            return (True, title, candidate, None)

                    # When we don't have a verified duplicate-selection index,
                    # let Instapaper resolve the first matching occurrence.
                    m.create_highlight(candidate)
                    return (True, title, candidate, None)
                except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as e:
                    last_error = str(e)
                    continue

            return (False, title, highlight_text, last_error or "Unknown highlight error")
        else:
            return (False, None, highlight_text, "Current index is out of range")

    def archive_current_bookmark(self):
        """Archives the currently selected bookmark. Returns (success, title, error_msg)."""
        marks = self._get_bookmarks()
        if not marks:
            return (False, None, "No bookmarks found")

        if 0 <= self.current_index < len(marks):
            m = marks[self.current_index]
            title = m.title
            try:
                m.archive()
                return (True, title, None)
            except (AttributeError, ValueError, RuntimeError, OSError) as e:
                return (False, title, str(e))
        else:
            return (False, None, "Current index is out of range")

    def get_bookmark_count(self):
        """Gets the total number of bookmarks."""
        marks = self._get_bookmarks()
        return len(marks) if marks else 0

    def get_current_index(self):
        """Gets the current bookmark index (0-based)."""
        return self.current_index

    def is_valid_index(self):
        """Checks if the current index is valid."""
        marks = self._get_bookmarks()
        return marks is not None and 0 <= self.current_index < len(marks)

    def get_current_bookmark_info(self):
        """Gets info about the current bookmark.

        Returns (title, url, index, total_count) or None.
        """
        marks = self._get_bookmarks()
        if not marks or not 0 <= self.current_index < len(marks):
            return None

        m = marks[self.current_index]
        return (str(m.title), str(m.url), self.current_index, len(marks))

    def set_bookmark_by_number(self, bookmark_number):
        """Set the current bookmark by its number (1-based).

        Args:
            bookmark_number: The bookmark number (1-based) to jump to.

        Returns:
            True if successful, False if the number is out of range.
        """
        marks = self._get_bookmarks()
        if not marks:
            return False

        # Convert 1-based to 0-based index
        index = bookmark_number - 1

        if 0 <= index < len(marks):
            self.current_index = index
            return True
        return False

    def get_article_by_number(self, bookmark_number):
        """Get the article content for a specific bookmark number (1-based).

        Args:
            bookmark_number: The bookmark number (1-based) to get content for.

        Returns:
            Article text if successful, None if the number is out of range.
        """
        marks = self._get_bookmarks()
        if not marks:
            return None

        # Convert 1-based to 0-based index
        index = bookmark_number - 1

        if 0 <= index < len(marks):
            m = marks[index]
            return str(m.text)
        return None

    def _load_spacy_model(self):
        """Lazy load the spaCy model when needed."""
        if self._nlp is None:
            self._nlp = spacy.load("en_core_web_sm")
        return self._nlp

    def parse_current_article_sentences(self):
        """Parse the current article into sentences using spaCy.

        Returns:
            list[str]: A list of sentence strings, or None if no article is available.
        """
        article_text = self.get_current_article()
        if not article_text:
            return None

        # Load spaCy model
        nlp = self._load_spacy_model()

        # Process the text
        doc = nlp(article_text)

        # Extract sentences
        sentences = []
        for sent in doc.sents:
            text = sent.text.strip()
            if text:
                sentences.append(text)

        return sentences if sentences else None
