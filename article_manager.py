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

    def create_highlight_for_current(self, highlight_text, position=0):
        """Creates a highlight for the current bookmark.

        Args:
            highlight_text: The text to highlight.
            position: Optional. The 0-indexed character position of the text in the content.

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
            try:
                # Try creating highlight without position parameter
                # The Instapaper API may be strict about position matching
                m.create_highlight(highlight_text)
                return (True, title, highlight_text, None)
            except (AttributeError, ValueError, RuntimeError, OSError) as e:
                return (False, title, highlight_text, str(e))
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
