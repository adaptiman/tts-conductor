#!/bin/bash
# Run all linting and formatting tools

set -euo pipefail

CHECK_MODE=0
if [[ "${1:-}" == "--check" ]]; then
	CHECK_MODE=1
elif [[ $# -gt 0 ]]; then
	echo "Usage: $0 [--check]"
	exit 2
fi

mapfile -t PY_FILES < <(git ls-files '*.py')

if [ ${#PY_FILES[@]} -eq 0 ]; then
	echo "No tracked Python files found."
	exit 0
fi

if [[ $CHECK_MODE -eq 1 ]]; then
	echo "🔎 Running flake8 (style checking)..."
	flake8 "${PY_FILES[@]}"

	echo "🔍 Running pylint (comprehensive linting)..."
	pylint "${PY_FILES[@]}" --exit-zero

	echo "🏷️  Running mypy (type checking, informational)..."
	mypy "${PY_FILES[@]}" --ignore-missing-imports || true

	echo "✅ Check-only linting complete!"
	exit 0
fi

echo "🔍 Running isort (import sorting)..."
isort "${PY_FILES[@]}"

echo "🎨 Running black (code formatting)..."
black "${PY_FILES[@]}"

echo "🔎 Running flake8 (style checking)..."
flake8 "${PY_FILES[@]}"

echo "🔍 Running pylint (comprehensive linting)..."
pylint "${PY_FILES[@]}" --exit-zero

echo "🏷️  Running mypy (type checking)..."
mypy "${PY_FILES[@]}" --ignore-missing-imports

echo "✅ Linting complete!"