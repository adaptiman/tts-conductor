# Development Tools Configuration

This project uses several Python development tools to maintain code quality:

## Installed Tools

- **flake8**: Style guide enforcement (PEP 8)
- **black**: Code formatter  
- **isort**: Import statement organizer
- **pylint**: Comprehensive code analysis
- **mypy**: Static type checker

## Configuration Files

- `.flake8`: Flake8 configuration
- `pyproject.toml`: Configuration for black, isort, pylint, and mypy

## Usage

### Run all linting tools:
```bash
./lint.sh
```

### Run individual tools:
```bash
# Format code
black ip_conductor.py article_manager.py

# Sort imports  
isort ip_conductor.py article_manager.py

# Style checking
flake8 ip_conductor.py article_manager.py

# Comprehensive linting
pylint ip_conductor.py article_manager.py

# Type checking
mypy ip_conductor.py article_manager.py --ignore-missing-imports
```

### VS Code Integration

These tools integrate well with VS Code. Install the following extensions:
- Python (ms-python.python)
- Pylint (ms-python.pylint) 
- Black Formatter (ms-python.black-formatter)
- isort (ms-python.isort)
- Mypy Type Checker (ms-python.mypy-type-checker)

The tools will automatically run in VS Code and show warnings/errors in real-time.

## Configuration Details

- Line length set to 88 characters (black's default)
- Import sorting follows black-compatible style
- Pylint configured to ignore overly strict rules for this project type
- Mypy set to ignore missing imports for third-party libraries