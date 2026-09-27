#!/usr/bin/env bash
# Run every Python test suite, each in its own pytest process.
# Separate processes keep per-package conftest.py modules from shadowing each other.
# Usage: scripts/test-python.sh [extra pytest args...]
set -uo pipefail
cd "$(dirname "$0")/.."

suites=(
  packages/engine/tests
  packages/investigator/tests
  packages/demo-data/tests
  apps/api/tests
  apps/mcp/tests
  tests/evals
)

failed=()
for suite in "${suites[@]}"; do
  if [ ! -d "$suite" ] || [ -z "$(find "$suite" -name 'test_*.py' -print -quit)" ]; then
    echo "== $suite: no tests, skipping"
    continue
  fi
  echo "== $suite"
  if ! uv run pytest "$suite" "$@"; then
    failed+=("$suite")
  fi
done

if [ ${#failed[@]} -gt 0 ]; then
  echo "FAILED suites: ${failed[*]}"
  exit 1
fi
echo "all Python suites passed"
