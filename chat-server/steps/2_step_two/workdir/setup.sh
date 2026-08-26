#!/bin/bash
# CHAT-003 fix: clear verifier leaks from step1 before step2 agent starts
# Step1's verifier leaves /tests/test_outputs.py, test.sh, CTRF report, and reward.txt
# which are readable/writable by root agent and give away Turn2 assertions.
set -e
echo "[setup] Cleaning leaked verifier artifacts from Turn1..."
rm -rf /tests/* 2>/dev/null || true
rm -rf /logs/verifier/* 2>/dev/null || true
rm -rf /tmp/codimango/* 2>/dev/null || true
# Keep data dir but ensure verifier dirs exist
mkdir -p /app/data /tmp/codimango/gocache /tmp/codimango/gopath /logs/verifier /tests
# Also clear any leftover lock files
rm -f /app/data/*.lock /app/data/global.lock 2>/dev/null || true
echo "[setup] Cleaned. /tests and /logs/verifier empty, ready for Turn2"
