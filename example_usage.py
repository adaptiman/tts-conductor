"""Example showing how to use ArticleManager in other programs."""

from article_manager import ArticleManager


def example_usage():
    """Demonstrates various ways to use the ArticleManager class."""

    # Initialize the manager
    print("Creating ArticleManager instance...")
    try:
        manager = ArticleManager(
            bookmark_limit=10
        )  # Limit to 10 bookmarks for this example
        print("✅ ArticleManager initialized successfully!")
    except (
        AttributeError,
        ValueError,
        RuntimeError,
        OSError,
        KeyError,
        FileNotFoundError,
    ) as e:
        print(f"❌ Failed to initialize ArticleManager: {e}")
        return

    # Example 1: Get current bookmark info (new data-focused API)
    print("\n📖 Current bookmark info:")
    info = manager.get_current_bookmark_info()
    if info:
        title, url, index, total = info
        print(f"Title: {title}")
        print(f"URL: {url}")
        print(f"Position: {index + 1} of {total}")
    else:
        print("No bookmarks available")

    # Example 2: Navigate through bookmarks programmatically
    print("\n🔄 Navigating bookmarks...")
    if manager.next_bookmark():
        title = manager.get_current_title()
        print(f"Moved to next bookmark: {title}")
    else:
        print("Already at last bookmark")

    if manager.prev_bookmark():
        title = manager.get_current_title()
        print(f"Moved back to previous bookmark: {title}")
    else:
        print("Already at first bookmark")

    # Example 3: Get all bookmarks (returns data, doesn't print)
    print("\n📚 All bookmarks:")
    bookmarks = manager.get_bookmarks_list()
    if bookmarks:
        for i, title in enumerate(bookmarks, 1):
            print(f"{i}. {title}")
        print(f"Total: {len(bookmarks)} bookmarks")
    else:
        print("No bookmarks found")

    # Example 4: Add a new bookmark programmatically
    print("\n➕ Adding a bookmark programmatically...")
    # Uncomment the next line to actually add a bookmark
    # success, url, error = manager.add_bookmark_url("https://example.com")
    # if success:
    #     print(f"Successfully added: {url}")
    # else:
    #     print(f"Failed to add bookmark: {error}")
    print("(Commented out to avoid adding test bookmarks)")

    # Example 5: Working with bookmark operations
    print("\n🎮 Bookmark operations example:")

    # Get current article content
    article = manager.get_current_article()
    if article:
        print(f"Article length: {len(article)} characters")
        print(f"Preview: {article[:100]}...")
    else:
        print("No article content available")

    # Check bookmark count and current position
    total_count = manager.get_bookmark_count()
    current_index = manager.get_current_index()
    is_valid = manager.is_valid_index()

    print(f"Total bookmarks: {total_count}")
    print(f"Current index: {current_index}")
    print(f"Index is valid: {is_valid}")

    # Example 6: Navigation with feedback
    print("\n🧭 Smart navigation:")
    print("Going to first bookmark...")
    if manager.first_bookmark():
        info = manager.get_current_bookmark_info()
        if info:
            print(f"Now at: {info[0]} (1 of {info[3]})")
    else:
        print("No bookmarks to navigate to")

    print("Going to last bookmark...")
    if manager.last_bookmark():
        info = manager.get_current_bookmark_info()
        if info:
            print(f"Now at: {info[0]} ({info[2] + 1} of {info[3]})")
    else:
        print("No bookmarks to navigate to")

    print("\n✅ Example completed!")
    print("👍 Note: All operations return data instead of printing directly")
    print(
        "   This makes ArticleManager perfect for integration with other applications!"
    )


if __name__ == "__main__":
    example_usage()
