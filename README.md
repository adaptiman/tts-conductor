# Instapaper Reader Console App

A Python console application for reading and managing Instapaper articles.

## Architecture

The application is built with a modular design:

- **`article_manager.py`** - Contains the `ArticleManager` class with all Instapaper functionality
- **`ip_conductor.py`** - Console interface that uses the `ArticleManager` class
- **`example_usage.py`** - Demonstrates how to use `ArticleManager` in other programs

This design allows you to easily integrate Instapaper functionality into other Python applications by importing the `ArticleManager` class.

## Setup

### Prerequisites
- Python 3.12 or higher
- pip and venv

### Installation

1. Clone or download this repository:
   ```bash
   cd /path/to/ip-conductor
   ```

2. Create a virtual environment:
   ```bash
   python3 -m venv .venv
   ```

3. Activate the virtual environment:
   ```bash
   source .venv/bin/activate  # On Linux/macOS/WSL
   # or
   .venv\Scripts\activate     # On Windows
   ```

4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

5. Download the spaCy language model:
   ```bash
   python -m spacy download en_core_web_sm
   ```

### Configuration

1. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your Instapaper credentials:
   ```bash
   INSTAPAPER_USERNAME=your_email@example.com
   INSTAPAPER_PASSWORD=your_password
   INSTAPAPER_CONSUMER_KEY=your_consumer_key
   INSTAPAPER_CONSUMER_SECRET=your_consumer_secret
   ```

3. (Optional) Configure speak mode line width:
   ```bash
   SPEAK_LINE_WIDTH=70  # Default is 70 characters
   ```

   This controls how text wraps in speak mode. Adjust based on your terminal width and reading preference.

**Note**: Never commit your `.env` file to version control. It's already included in `.gitignore`.

## Usage

Activate the virtual environment (if not already active):
```bash
source .venv/bin/activate
```

Run the application:
```bash
python ip_conductor.py
```

### Available Commands

#### Article Management
- `articles` / `bookmarks` / `a` - List all articles with numbers (up to 25 by default)
- `add` - Add a new article by entering a URL
- `delete` / `d` - Delete the currently selected article (with confirmation)
- `star` / `s` - Star the currently selected article
- `archive` / `c` - Archive the currently selected article
- `highlight` - Create a highlight for the current article (multi-line text input)
- `speak` / `k` - Enter sentence-by-sentence reading mode with highlighting support
- `speak <number>` / `k <number>` - Navigate to and speak a specific article by its number

#### Navigation
- `title` - Show current article title
- `<number>` - Navigate to article by number and display its title (e.g., `5` jumps to article 5)
- `next` / `n` - Move to next article
- `prev` / `previous` / `p` - Move to previous article
- `first` - Jump to first article
- `last` - Jump to last article
- `read` / `r` - Read current article content
- `read <number>` / `r <number>` - Navigate to and read a specific article by its number from the list

#### System
- `exit` - Quit the application

### Keyboard Shortcuts

For faster navigation, single-letter shortcuts are available for common commands:
- `a` - Articles/bookmarks list
- `n` - Next article
- `p` - Previous article
- `d` - Delete current article
- `s` - Star current article
- `c` - Archive current article
- `r` - Read current article
- `k` - Speak current article

**With article numbers:**
- `r 3` - Read article 3
- `k 5` - Speak article 5

### Speak Mode

Speak mode provides an interactive sentence-by-sentence reading experience with intelligent sentence parsing powered by spaCy:

1. Enter speak mode: `speak`
2. Navigate and highlight using keyboard commands:
   - **SPACE** - Display next sentence
   - **B** - Go back to previous sentence
   - **H** - Highlight current sentence (saves to Instapaper)
   - **Q** - Quit speak mode

Each sentence displays with its position in the article:
```
[sentence_number/total_sentences]
Sentence text appears here.
```

When you highlight a sentence with **H**, a confirmation message appears, and you can continue navigating with SPACE or B.

### Features

- **Environment-based configuration**: Secure credential storage using `.env` files
- **Virtual environment support**: Isolated dependencies per project
- **Numbered article listing**: Articles are displayed with numbers for easy reference
- **Quick navigation**: Jump to any article by simply entering its number
- **Direct article access**: Jump to and read any article by its number
- **Speak mode**: Interactive sentence-by-sentence reading with NLP-powered sentence parsing
- **Smart highlighting**: Highlight sentences directly from speak mode with automatic syncing to Instapaper
- **Configurable article limit**: The application fetches 25 articles by default (configurable in `ArticleManager` initialization)
- **Error handling**: Comprehensive error handling for network issues, API errors, and invalid operations
- **Interactive highlights**: Create multi-line highlights by entering text and pressing Enter twice to finish
- **Confirmation prompts**: Safe deletion with confirmation prompts

### Example Workflow

```bash
# Activate the virtual environment
source .venv/bin/activate

# Start the application
python ip_conductor.py

# List all articles with numbers (using shortcut)
> a
1. Understanding Python Decorators
2. Introduction to Machine Learning
3. Web Development Best Practices
4. Advanced Git Techniques
5. Docker for Beginners

# Quick jump to article 3 by entering just the number
> 3
Web Development Best Practices

# Read the current article using shortcut
> r

# Or jump and read in one command using shortcut
> r 5
[Displays content of "Docker for Beginners"]

# Enter speak mode for sentence-by-sentence reading using shortcut
> k
[1/350] [0,45]
Docker is a platform for developing applications.
# Press SPACE to see next sentence
# Press H to highlight current sentence
# Press B to go back to previous sentence
# Press Q to quit speak mode

# Or speak a specific article directly
> k 2
[Opens speak mode for article 2: "Introduction to Machine Learning"]

# Navigate to next article using shortcut
> n
[Now at article 6]

# Create a highlight
> highlight
Enter the text you want to highlight (press Enter twice to finish):
This is important text
that I want to remember.

# Star the article using shortcut
> s

# Archive when done using shortcut
> c

# Exit
> exit
```

## Dependencies

### Core Application Dependencies
- `instapaper==0.5` - Instapaper API client for bookmark management
- `oauth2==1.9.0.post1` - OAuth authentication for Instapaper API
- `httplib2==0.31.0` - HTTP client library for API requests
- `python-dotenv==1.2.1` - Environment variable management for configuration
- `spacy==3.8.11` - Natural language processing for sentence parsing
- `en-core-web-sm` - English language model for spaCy (downloaded separately)
- `setuptools==80.9.0` - Python package utilities (required for Python 3.12+)

### Development and Code Quality Tools
- `black==25.11.0` - Code formatter for consistent Python code style
- `flake8==7.3.0` - Style guide enforcement (PEP 8 compliance)
- `isort==7.0.0` - Import statement organizer and sorter
- `mypy==1.18.2` - Static type checker for Python
- `pylint==4.0.3` - Comprehensive code analysis and linting

### Additional Dependencies
The application also includes various supporting packages for spaCy, HTTP handling, and data processing. See `requirements.txt` for the complete list of dependencies with exact versions.

All dependencies are listed in `requirements.txt` and will be installed automatically with `pip install -r requirements.txt`.

## Customization

### Article Limit
To change the number of articles fetched, pass a different limit when creating the `ArticleManager` instance:

```python
# In ip_conductor.py main() function
manager = ArticleManager(bookmark_limit=50)  # Change from default 25 to 50
```

## Using ArticleManager in Other Programs

The `ArticleManager` class can be easily imported and used in other Python applications. Make sure your `.env` file is properly configured in your project directory.

```python
from article_manager import ArticleManager

# Create an instance
manager = ArticleManager(bookmark_limit=25)

# Get article information
title = manager.get_current_title()
article_text = manager.get_current_article()
article_list = manager.get_bookmarks_list()

# Navigate articles
manager.next_bookmark()
manager.prev_bookmark()
manager.first_bookmark()
manager.last_bookmark()

# Jump to a specific article by number (1-based)
manager.set_bookmark_by_number(5)

# Manage articles
success, url, error = manager.add_bookmark_url("https://example.com")
success, title, error = manager.star_current_bookmark()
success, title, error = manager.archive_current_bookmark()
success, title, error = manager.delete_current_bookmark()

# Create highlights
success, title, highlight, error = manager.create_highlight_for_current("Important text")

# Parse article into sentences for speak mode functionality
sentences = manager.parse_current_article_sentences()
# Returns list of sentence strings: ["First sentence.", "Second sentence.", ...]

# Access the Instapaper client directly for advanced operations
bookmarks = manager.instapaper_client.bookmarks(limit=10)
```

See `example_usage.py` for a complete demonstration of using `ArticleManager` programmatically.

### Adding New Commands
The application is designed to be easily extensible. To add new commands:

1. Add a new method to the `ArticleManager` class in `article_manager.py`
2. Add error handling using try-except blocks with appropriate exception types
3. Add a command handler function in `ip_conductor.py` (following the pattern of existing handlers)
4. Add the command to the main command loop in the `run_console()` function
5. Update the help messages to include the new command

## Development Tools and Code Quality

This project includes comprehensive code quality tools to maintain clean, consistent, and error-free Python code:

### Available Tools
- **Black**: Automatic code formatting for consistent style
- **isort**: Import statement organization and sorting
- **Flake8**: Style guide enforcement (PEP 8 compliance)
- **Pylint**: Comprehensive code analysis and quality checking
- **Mypy**: Static type checking for better code reliability

### Usage

#### Run all linting and formatting tools:
```bash
./lint.sh
```

#### Run individual tools:
```bash
# Format code automatically
black ip_conductor.py article_manager.py

# Sort and organize imports
isort ip_conductor.py article_manager.py

# Check code style (PEP 8)
flake8 ip_conductor.py article_manager.py

# Comprehensive code analysis
pylint ip_conductor.py article_manager.py

# Static type checking
mypy ip_conductor.py article_manager.py --ignore-missing-imports
```

### Configuration
- **`.flake8`**: Flake8 configuration with 88-character line length
- **`pyproject.toml`**: Centralized configuration for Black, isort, Pylint, and Mypy
- **`lint.sh`**: Convenient script to run all tools in sequence

### VS Code Integration
The project includes VS Code settings that integrate these tools for real-time feedback. Install these recommended extensions:
- Python (ms-python.python)
- Pylint (ms-python.pylint)
- Black Formatter (ms-python.black-formatter)
- isort (ms-python.isort)
- Mypy Type Checker (ms-python.mypy-type-checker)

For more details, see `LINTING.md`.

## VS Code Setup (WSL)

If you're using VS Code with WSL, the project includes VS Code settings in `.vscode/settings.json` that will:
- Automatically use the project's virtual environment
- Activate the venv when opening new terminals

This provides seamless integration without manual activation within VS Code.

## Troubleshooting

### Missing Environment Variables
If you see an error about missing environment variables, ensure:
1. Your `.env` file exists in the project root
2. All four required variables are set (USERNAME, PASSWORD, CONSUMER_KEY, CONSUMER_SECRET)
3. There are no extra spaces or quotes around the values

### Import Errors in WSL Terminal
If you get import errors when running from a WSL terminal outside VS Code:
```bash
cd /path/to/ip-conductor
source .venv/bin/activate
```

The virtual environment must be activated to access the installed packages.