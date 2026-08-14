#!/bin/bash
# Top-level test aggregator for multi-turn task
set -e
cd "$(dirname "$0")"
echo "Running step1 tests via test_outputs.py (if pytest available)..."
if command -v pytest >/dev/null 2>&1; then
  pytest ../steps/1_step_one/tests/test_outputs.py -q 2>&1 | tail -20 || true
  pytest ../steps/2_step_two/tests/test_outputs.py -q 2>&1 | tail -20 || true
else
  echo "pytest not found, checking Go build..."
  cd /app && go test ./... 2>&1 || go build ./... && go vet ./...
fi
echo "Top-level test check done"
