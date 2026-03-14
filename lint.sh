#!/bin/bash
# Run all linting and formatting tools

echo "🔍 Running isort (import sorting)..."
isort ip_conductor.py article_manager.py example_usage.py

echo "🎨 Running black (code formatting)..."
black ip_conductor.py article_manager.py example_usage.py

echo "🔎 Running flake8 (style checking)..."
flake8 ip_conductor.py article_manager.py example_usage.py

echo "🔍 Running pylint (comprehensive linting)..."
pylint ip_conductor.py article_manager.py example_usage.py --exit-zero

echo "🏷️  Running mypy (type checking)..."
mypy ip_conductor.py article_manager.py example_usage.py --ignore-missing-imports

echo "✅ Linting complete!"